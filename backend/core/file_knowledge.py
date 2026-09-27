"""관리자 기본지식에 적힌 '단일 파일 설명'을 차트용 파일 카탈로그로 만든다.

예) ``AA_yld.csv 는 AA product 의 yld 가 있는 파일. tkouttimeA 가 tkout_time 이고
yld01 이 수율값이다.`` → 파일 AA_yld.csv · 제품 AA · 시간 열 tkouttimeA · 측정 열
yld01(수율값).

규칙으로만 읽는다(LLM 없음). 문장에 나온 열 이름은 실제 파일 헤더에 있는 것만
인정하고, 헤더에 없는 이름·찾지 못한 파일은 경고로 돌려 관리자가 고치게 한다.
홈 챗은 ``status == "ready"`` 인 항목만 차트에 쓴다.
"""
from __future__ import annotations

import re
import threading
import time

from core import domain_knowledge
from core.utils import SINGLE_FILE_ROOT, resolve_db_single_file

FILE_NAME = re.compile(r"(?<![\w./-])((?:[\w-]+/){0,3}[\w.-]*\w\.(?:csv|parquet))(?![A-Za-z0-9_])", re.I)
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
BULLET = re.compile(r"^(\s*)(?:[-*+]|\d+[.)])\s+")
# 열 이름 뒤 "는/가/=/:/→/|" 다음 의미. "이고·이며·이다" 앞에서 끊는다.
PARTICLE = r"(?:\s*(?:열|컬럼|column|col)(?![\w]))?\s*(?:은|는|이(?![고며다])|가|=>|->|→|=|:|：|\|)\s*"
STOP = r"(?=\s*(?:이고|이며|이다|입니다|이에요|이야|임(?![\w])|[,;|，、\n()]|\.(?:\s|$)|$))"
MEANING_AFTER = re.compile(r"^" + PARTICLE + r"(.+?)" + STOP)
PAREN_AFTER = re.compile(r"^\s*\(\s*([^()\n]{1,60}?)\s*\)")
# "수율값은 yld01" 처럼 의미가 앞에 오는 경우(같은 절 안에서만).
MEANING_BEFORE = re.compile(r"([^\s,;|:.()]{1,30})\s*(?:열|컬럼|column)?\s*(?:은|는|이|가|=|:)\s*$")
PRODUCT_AFTER = re.compile(r"(?:product|제품|프로덕트)\s*[:=]?\s*([A-Za-z][\w.-]{0,40})", re.I)
PRODUCT_BEFORE = re.compile(r"(?<![\w.])([A-Za-z][\w.-]{0,40})\s*(?:product|제품|프로덕트)", re.I)
TOPIC = re.compile(r"의\s*([\w가-힣]{1,20}?)\s*(?:값|데이터|data)?\s*(?:이|가)?\s*(?:있는|들어\s*있는|담긴|저장된|기록된)", re.I)

ROLE_WORDS = {
    "time": {"tkouttime", "tkout", "trackouttime", "trackout", "측정시간", "측정시각", "시간", "시각", "일시",
             "날짜", "date", "time", "datetime", "timestamp", "측정일", "측정일시"},
    "root_lot_id": {"rootlotid", "rootlot", "루트랏", "루트lot", "rootlot번호"},
    "lot_id": {"lotid", "lot", "fablot", "fablotid", "랏", "랏id", "랏번호", "lot번호"},
    "wafer_id": {"waferid", "wafer", "웨이퍼", "웨이퍼번호", "wafer번호", "waferno", "wf", "wfid", "slot", "슬롯", "슬롯번호"},
    "product": {"product", "제품", "제품명", "productname"},
}
ROLE_LABELS = {"time": "시간", "root_lot_id": "Root Lot", "lot_id": "Lot", "wafer_id": "Wafer", "product": "제품"}
MEASURE_SUFFIX = re.compile(r"\s*(?:값|value|열|컬럼|column|데이터|data)$", re.I)

_CACHE: dict = {}
_LOCK = threading.Lock()


def _clean(text: str) -> str:
    """Markdown 강조 기호를 걷어낸다(열 이름 안의 밑줄은 유지)."""
    text = text.replace("`", "").replace("**", "")
    return re.sub(r"(?<![\w])__(?=\w)|(?<=\w)__(?![\w])", "", text)


def _compact(value: str) -> str:
    return re.sub(r"[\s_\-./]+", "", str(value or "").casefold())


def _role(meaning: str) -> str:
    compact = _compact(re.sub(r"(?:열|컬럼|column|col)$", "", meaning.strip(), flags=re.I))
    if "tkout" in compact:
        return "time"
    for role, words in ROLE_WORDS.items():
        if compact in words:
            return role
    return ""


def _scopes(body: str) -> dict[str, list[str]]:
    """파일 이름별 설명 범위. 제목에 나오면 그 절 전체, 본문이면 다음 파일·빈 줄 전까지."""
    lines = _clean(body).splitlines()
    scopes: dict[str, list[str]] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        names = list(dict.fromkeys(m.group(1) for m in FILE_NAME.finditer(line)))
        if not names:
            index += 1
            continue
        heading = HEADING.match(line)
        bullet = BULLET.match(line)
        end = index + 1
        while end < len(lines):
            nxt = lines[end]
            nxt_heading = HEADING.match(nxt)
            if heading:
                other_file = any(m.group(1) not in names for m in FILE_NAME.finditer(nxt))
                if (nxt_heading and len(nxt_heading.group(1)) <= len(heading.group(1))) or other_file:
                    break
            elif not nxt.strip() or nxt_heading or FILE_NAME.search(nxt):
                break
            elif bullet and (sibling := BULLET.match(nxt)) and len(sibling.group(1)) <= len(bullet.group(1)):
                # 목록 항목의 파일은 그 항목과 더 깊은 하위 항목까지만 설명한다.
                break
            end += 1
        block = "\n".join(lines[index:end])
        for name in names:
            # 한 줄에 파일이 여럿이면 그 줄은 파일별로 자른다.
            text = block if len(names) == 1 else _own_segment(line, name, names)
            scopes.setdefault(name, []).append(text)
        index = end if heading or len(names) == 1 else index + 1
    return scopes


def _own_segment(line: str, name: str, names: list[str]) -> str:
    start = line.find(name)
    ends = [line.find(other, start + len(name)) for other in names if other != name]
    ends = [pos for pos in ends if pos > start]
    return line[start:min(ends) if ends else len(line)]


def _column_pattern(column: str) -> re.Pattern:
    # 한글 조사가 바로 붙으므로(yld01이) 경계는 ASCII 단어 문자로만 본다.
    return re.compile(r"(?<![A-Za-z0-9_.])" + re.escape(column) + r"(?![A-Za-z0-9_])", re.I)


def _meanings(text: str, columns: list[str], file_name: str) -> tuple[dict, list[dict], list[str]]:
    roles: dict[str, dict] = {}
    measures: list[dict] = []
    others = sorted((c for c in columns if len(c) >= 2), key=len, reverse=True)
    for column in others:
        for match in _column_pattern(column).finditer(text):
            after = text[match.end():]
            meaning = ""
            found = MEANING_AFTER.match(after) or PAREN_AFTER.match(after)
            if found:
                meaning = found.group(1).strip()
            if not meaning:
                clause = re.split(r"[,;|\n]|\.(?:\s|$)|이고|이며", text[:match.start()])[-1]
                before = MEANING_BEFORE.search(clause)
                meaning = before.group(1).strip() if before else ""
            # 의미 안에 다른 실제 열 이름이 끼면 그 앞에서 자른다(이어진 문장 방지).
            for other in others:
                if other != column:
                    hit = _column_pattern(other).search(meaning)
                    if hit:
                        meaning = meaning[:hit.start()].strip()
            meaning = re.sub(r"(?:이|가|은|는|을|를)$", "", meaning).strip(" '\"")
            if not meaning or meaning.casefold() == column.casefold() or FILE_NAME.fullmatch(meaning):
                continue
            role = _role(meaning)
            if role:
                roles.setdefault(role, {"column": column, "meaning": meaning, "basis": "기본지식"})
            else:
                label = MEASURE_SUFFIX.sub("", meaning).strip() or meaning
                measures.append({"column": column, "label": meaning,
                                 "aliases": sorted({meaning, label, column}, key=len, reverse=True)})
            break
    # 문장에 나왔지만 실제 헤더에 없는 열 이름(오타·다른 파일) — 관리자 확인용.
    unknown = []
    folded = {c.casefold() for c in columns}
    topics = {t.casefold() for t in TOPIC.findall(text)}
    for match in re.finditer(r"(?<![A-Za-z0-9_.])([A-Za-z_][A-Za-z0-9_]{2,60})" + PARTICLE, text):
        token = match.group(1)
        # 일반 영어 단어(Reformatter 는 …)는 빼고 열 이름처럼 생긴 것만: 숫자·밑줄·전부 대문자·camelCase.
        # 표 칸(| x |)이나 "x: …", "x = …" 형식이면 열 이름을 적은 것으로 본다.
        column_like = (re.search(r"[0-9_]", token) or token.isupper() or re.search(r"[a-z][A-Z]", token)
                       or re.search(r"[|:=→>]", match.group(0)[len(token):]))
        if (column_like and token.casefold() not in folded and token.casefold() not in topics and token.casefold() != file_name.casefold()
                and not _role(token) and not re.fullmatch(r"(?:product|file|csv|parquet|column|col)", token, re.I)):
            unknown.append(token)
    return roles, measures, list(dict.fromkeys(unknown))[:8]


def _describe(name: str, texts: list[str]) -> dict:
    text = "\n".join(texts)
    entry = {"file": name.rsplit("/", 1)[-1], "mention": name, "path": "", "root": SINGLE_FILE_ROOT,
             "product": "", "description": "", "topics": [], "roles": {}, "measures": [],
             "columns": [], "warnings": [], "status": "ready"}
    first = texts[0].split("\n", 1)[0]
    entry["description"] = re.sub(r"^#+\s*|^[-*]\s*|\|", " ", first).strip()[:200]
    product = PRODUCT_BEFORE.search(text) or PRODUCT_AFTER.search(text)
    if product and not FILE_NAME.fullmatch(product.group(1)):
        entry["product"] = product.group(1)
    entry["topics"] = list(dict.fromkeys(t.strip() for t in TOPIC.findall(text) if t.strip()))[:4]
    path, candidates = _locate(name)
    if not path:
        # 열 설명 없이 이름만 나온 없는 파일(예: 참고용 기준정보 이름)은 언급으로만 둔다.
        described = re.search(r"(?<![A-Za-z0-9_.])[A-Za-z_][A-Za-z0-9_]{1,60}" + PARTICLE + r"\S", text)
        entry["status"] = "missing_file" if described or candidates else "mentioned"
        if entry["status"] == "mentioned":
            return entry
        entry["warnings"].append(
            f"DB 루트에서 {name} 파일을 찾지 못했습니다." if not candidates else
            f"{name} 이름의 파일이 여러 폴더에 있습니다: {', '.join(candidates[:4])}. 폴더를 포함한 경로로 적어 주세요.")
        return entry
    entry["path"] = path
    try:
        from core import duckdb_engine
        columns, _ = duckdb_engine.inspect_files([resolve_db_single_file(path)])
    except Exception as exc:  # noqa: BLE001 - 읽기 실패는 관리자 화면에 그대로 보인다
        entry["status"] = "unreadable"
        entry["warnings"].append(f"파일 헤더를 읽지 못했습니다: {str(exc)[:160]}")
        return entry
    entry["columns"] = list(columns)
    roles, measures, unknown = _meanings(text, list(columns), entry["file"])
    # 표준 이름의 열은 설명이 없어도 결합 키·시간으로 쓴다.
    for column in columns:
        role = {"tkout_time": "time", "root_lot_id": "root_lot_id", "lot_id": "lot_id",
                "fab_lot_id": "lot_id", "wafer_id": "wafer_id", "product": "product"}.get(column.casefold())
        if role and role not in roles and all(m["column"] != column for m in measures):
            roles[role] = {"column": column, "meaning": column, "basis": "열 이름"}
    entry["roles"], entry["measures"] = roles, measures
    if len(measures) == 1:
        measures[0]["aliases"] = sorted(set(measures[0]["aliases"]) | set(entry["topics"]), key=len, reverse=True)
    if not measures and not unknown and all(r["basis"] == "열 이름" for r in roles.values()):
        # 설명 없이 이름만 언급된 파일(기준정보 참고 등)은 차트 대상이 아니다 — 경고로 어지럽히지 않는다.
        entry["status"] = "mentioned"
        return entry
    if unknown:
        entry["warnings"].append("파일 헤더에 없는 열 이름: " + ", ".join(unknown))
    if not measures:
        entry["status"] = "no_measure"
        entry["warnings"].append("측정값 열을 찾지 못했습니다. 예: 'yld01 이 수율값이다'처럼 실제 열 이름과 의미를 적어 주세요.")
    if "time" not in roles:
        entry["warnings"].append("시간 열 설명이 없어 Trend를 그릴 수 없습니다. 예: 'tkouttimeA 가 tkout_time'.")
    if "wafer_id" not in roles or not ({"root_lot_id", "lot_id"} & set(roles)):
        entry["warnings"].append("Lot·Wafer 열 설명이 없어 Inline 등 다른 데이터와 Corr 결합을 할 수 없습니다.")
    return entry


def _locate(name: str) -> tuple[str, list[str]]:
    """DB 루트 기준 상대경로. 이름만 적었으면 루트와 바로 아래 폴더에서 찾는다."""
    if resolve_db_single_file(name):
        return name, []
    if "/" in name:
        return "", []
    from core.paths import PATHS
    found = []
    try:
        for child in sorted(PATHS.db_root.iterdir(), key=lambda p: p.name.casefold()):
            if child.is_dir() and resolve_db_single_file(f"{child.name}/{name}"):
                found.append(f"{child.name}/{name}")
    except OSError:
        return "", []
    return (found[0], []) if len(found) == 1 else ("", found)


def parse(body: str) -> list[dict]:
    """본문에서 파일 설명을 읽어 실제 파일과 대조한 목록(저장 전 미리보기에도 쓴다)."""
    return [_describe(name, texts) for name, texts in _scopes(body or "").items()]


def _signature(entries: list[dict]) -> tuple:
    sig = []
    for entry in entries:
        fp = resolve_db_single_file(entry["path"]) if entry.get("path") else None
        try:
            stat = fp.stat() if fp else None
        except OSError:
            stat = None
        sig.append((entry["mention"], entry.get("path"), stat.st_mtime_ns if stat else 0, stat.st_size if stat else 0))
    return tuple(sig)


def catalog() -> dict:
    """저장된 기본지식의 파일 카탈로그(버전·파일 변경 시에만 다시 읽음).

    없던 파일이 나중에 생기는 경우도 있으므로 5분이 지나면 다시 대조한다."""
    doc = domain_knowledge.read_document()
    with _LOCK:
        cached = _CACHE.get("value")
        if (cached and cached["version"] == doc["version"] and time.monotonic() - cached["built_at"] < 300
                and cached["signature"] == _signature(cached["entries"])):
            return cached
    entries = parse(doc.get("body") or "")
    value = {"version": doc["version"], "entries": entries, "signature": _signature(entries), "built_at": time.monotonic()}
    with _LOCK:
        _CACHE["value"] = value
    return value


def public(entries: list[dict]) -> list[dict]:
    """관리자 화면용 요약(헤더 전체 목록 대신 개수)."""
    out = []
    order = {"ready": 0, "mentioned": 2}
    for entry in sorted(entries, key=lambda e: order.get(e["status"], 1)):
        out.append({key: entry[key] for key in ("file", "mention", "path", "product", "description", "topics",
                                                 "roles", "measures", "warnings", "status")}
                   | {"column_count": len(entry.get("columns") or []), "role_labels": ROLE_LABELS})
    return out
