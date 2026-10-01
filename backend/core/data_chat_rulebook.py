"""Safe CSV rulebook editing for the active Home agent.

The first turn only builds a persisted, reviewable proposal.  A separate
approval from the same authenticated user applies the exact proposed rows.
Browser supplied context is never used as authority; the proposal file owns
the actor, source digests, destinations and final CSV documents.
"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

from core import chat_table
from core.paths import PATHS
from core.utils import load_json, save_json


FEATURE = "rulebook.csv_update"
TTL_SECONDS = 1800
MAX_ROWS = 500
MAX_QUERY_ROWS = 100

_TAGGED = re.compile(r"(승인|취소)\s+([0-9a-f]{32})", re.I)
_APPROVE = re.compile(r"(?:승인|반영|적용|저장)(?:해|해줘|해주세요|합니다|할게요|하겠습니다)?[.!\s]*", re.I)
_CANCEL = re.compile(r"(?:취소|중단|보류|하지\s*마|하지\s*말아\s*줘)(?:해|해줘|해주세요)?[.!\s]*", re.I)
_ACTIONS = {
    "add": re.compile(r"(?:^|\s)(?:add|append)(?=\s|$)|(?:추가|등록)(?:해|해줘|해주세요|합니다|할게요)?(?=\s|$|[.!])", re.I),
    "update": re.compile(r"(?:^|\s)(?:update|edit)(?=\s|$)|(?:수정|변경|업데이트)(?:해|해줘|해주세요|합니다|할게요)?(?=\s|$|[.!])", re.I),
    "delete": re.compile(r"(?:^|\s)(?:delete|remove)(?=\s|$)|(?:삭제|제거)(?:해|해줘|해주세요|합니다|할게요)?(?=\s|$|[.!])", re.I),
}
_QUERY = re.compile(r"조회|목록|보여|찾아|검색|확인|list|show|find|search|query|몇\s*행", re.I)
_ROW_HEADERS = {"row", "rowno", "rownumber", "no", "행", "행번호", "번호"}

_FILES: dict[str, dict[str, Any]] = {
    "ppid_knob.csv": {"aliases": ("ppid_knob.csv", "ppid knob", "ppid_knob")},
    "Vehicle_matching.csv": {
        "aliases": ("vehicle_matching.csv", "vehicle matching", "vehicle_matching"),
    },
    "Inline_matching.csv": {
        "aliases": ("inline_matching.csv", "inline matching", "inline_matching"),
    },
    "vm_matching.csv": {"aliases": ("vm_matching.csv", "vm matching", "vm_matching")},
    "mask.csv": {"aliases": ("mask.csv", "mask")},
    "mask_info.csv": {"aliases": ("mask_info.csv", "mask info", "mask_info")},
}

_SCHEMA_KINDS = {
    "Vehicle_matching.csv": "step_matching",
    "Inline_matching.csv": "inline_matching",
    "vm_matching.csv": "vm_matching",
}

_EDITOR_PAGES = {
    "ppid_knob.csv": ("splittable", "valve"),
    "Vehicle_matching.csv": ("splittable", "valve", "matchfill"),
    "Inline_matching.csv": ("splittable", "matchfill"),
    "vm_matching.csv": ("splittable", "matchfill"),
    "mask.csv": ("valve", "filebrowser"),
    "mask_info.csv": ("valve",),
}

_REQUIRED_FIELDS = {
    "ppid_knob.csv": (("feature_name",), ("step_desc", "function_step")),
    "Vehicle_matching.csv": (("product", "vehicle"), ("step_id",), ("step_desc", "function_step")),
    "Inline_matching.csv": (("product", "vehicle"), ("step_id",)),
    "vm_matching.csv": (("step_desc",), ("item_id",)),
    "mask.csv": (("product",), ("reticle_id",), ("mask_version",)),
    "mask_info.csv": (("reticle_id",), ("category",)),
}


def _norm_name(value: object) -> str:
    return re.sub(r"[\s_.-]+", "", str(value or "")).casefold()


def _mentioned_file(text: str) -> str | None:
    value = str(text or "")
    found = []
    for filename, spec in _FILES.items():
        aliases = spec["aliases"]
        if filename == "mask.csv" and not re.search(
            r"파일|csv|매칭|룰북|수정|변경|추가|삭제|등록|편집", value, re.I
        ):
            aliases = tuple(alias for alias in aliases if alias != "mask")
        if any(re.search(rf"(?<![A-Za-z0-9_]){re.escape(alias)}(?![A-Za-z0-9_])", value, re.I)
               for alias in aliases):
            found.append(filename)
    return found[0] if len(found) == 1 else (None if found else "")


def _action(text: str) -> str:
    found = [name for name, pattern in _ACTIONS.items() if pattern.search(str(text or ""))]
    return found[0] if len(found) == 1 else ""


def _is_approval(text: str) -> bool:
    return bool(_APPROVE.fullmatch(str(text or "").strip()))


def _is_cancel(text: str) -> bool:
    return bool(_CANCEL.fullmatch(str(text or "").strip()))


def _user(request) -> dict | None:
    try:
        from core import auth
        return auth.current_user(request) if request is not None else None
    except Exception:
        return None


def _can_edit(user: dict | None, filename: str) -> bool:
    if not user:
        return False
    if str(user.get("role") or "") == "admin":
        return True
    try:
        from core import auth
        return any(auth.is_page_manager(user, page) for page in _EDITOR_PAGES.get(filename, ()))
    except Exception:
        return False


def _reply(message: str, context: dict, *, ok: bool = True, table: list[dict] | None = None,
           columns: list[str] | None = None, approval: dict | None = None, **tool_fields):
    from core.data_chat import reply
    tool = {"feature": FEATURE, "sources": tool_fields.pop("sources", []), **tool_fields}
    if table is not None:
        tool["table"] = {"rows": table, "total": len(table)}
        if columns:
            tool["table"]["columns"] = columns
    if approval:
        tool["approval"] = approval
    return reply(message, context=context, ok=ok, tool=tool,
                 interpretation={"summary": message, "source": ", ".join(tool.get("sources") or [])})


def _denied(context: dict):
    return _reply("이 매칭 CSV를 관리하는 페이지의 관리자 권한이 필요합니다.", context,
                  ok=False, blocked=True)


def _proposal_path(identifier: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", str(identifier or "")):
        raise ValueError("승인할 매칭 파일 미리보기가 없습니다. 변경 내용을 먼저 요청해 주세요.")
    return PATHS.data_root / "chat_proposals" / f"rulebook_{identifier}.json"


def _case_path(root: Path, filename: str) -> Path:
    target = root / filename
    if target.exists() or not root.is_dir():
        return target
    matches = [item for item in root.iterdir() if item.is_file() and item.name.casefold() == filename.casefold()]
    return matches[0] if len(matches) == 1 else target


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.is_file():
        return [], []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = [str(value or "").lstrip("\ufeff") for value in (reader.fieldnames or [])]
        if not columns or any(not column for column in columns) or len({_norm_name(c) for c in columns}) != len(columns):
            raise ValueError(f"{path.name} 머리글이 비어 있거나 중복되어 있어 안전하게 편집할 수 없습니다.")
        rows = []
        for raw in reader:
            if raw.get(None):
                raise ValueError(f"{path.name}에 따옴표로 감싸지 않은 쉼표 행이 있어 홈에서 편집할 수 없습니다.")
            rows.append({column: str(raw.get(column) or "") for column in columns})
    return columns, rows


def _csv_bytes(columns: list[str], rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({column: str(row.get(column) or "") for column in columns})
    return buf.getvalue().encode("utf-8-sig")


def _digest(path: Path) -> str:
    if not path.is_file():
        return "missing"
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _destinations(filename: str) -> list[dict[str, str]]:
    """The operator keeps one authoritative CSV per target in the DB root."""
    if filename not in _FILES:
        raise ValueError("지원하지 않는 매칭 파일입니다.")
    db_root = Path(PATHS.db_root)
    if filename == "ppid_knob.csv" or filename in _SCHEMA_KINDS:
        base_root = getattr(PATHS, "base_root", None)
        if base_root is not None:
            from app_v2.shared.source_adapter import resolve_existing_root

            db_root = resolve_existing_root("base", Path(base_root))
    active_name = filename
    kind = _SCHEMA_KINDS.get(filename)
    if kind:
        from core.file_check import _schema

        settings = _schema(Path(PATHS.data_root)).get(kind)
        if isinstance(settings, dict) and settings.get("file_name"):
            active_name = Path(str(settings["file_name"])).name
            if not active_name.casefold().endswith(".csv"):
                active_name += ".csv"
    primary = _case_path(db_root, active_name)
    return [{"path": str(primary), "label": f"원천 DB/{primary.name}"}]


def _source_document(destinations: list[dict[str, str]]) -> tuple[list[str], list[dict[str, str]]]:
    existing = next((Path(item["path"]) for item in destinations if Path(item["path"]).is_file()), None)
    return _read_csv(existing) if existing is not None else ([], [])


def _column_map(columns: list[str]) -> dict[str, str]:
    return {_norm_name(column): column for column in columns}


def _validate_changed_rows(filename: str, columns: list[str], affected: list[dict]) -> None:
    mapped = _column_map(columns)
    for names in _REQUIRED_FIELDS[filename]:
        if not any(_norm_name(name) in mapped for name in names):
            raise ValueError(f"{filename}에 필수 열이 없습니다: {' 또는 '.join(names)}")
    for change in affected:
        after = change.get("after") or {}
        if not after:
            continue
        for names in _REQUIRED_FIELDS[filename]:
            if not any(str(after.get(mapped[_norm_name(name)]) or "").strip()
                       for name in names if _norm_name(name) in mapped):
                raise ValueError(f"CSV {change['row_number']}행의 필수 값이 비어 있습니다: {' 또는 '.join(names)}")


def _table_parts(text: str, table: dict | None, action: str,
                 columns: list[str]) -> tuple[list[str], list[list[str]]]:
    if not table or not table.get("rows"):
        return [], []
    raw = table["rows"]
    header = [str(cell or "").strip() for cell in raw[0]]
    colmap = _column_map(columns)
    has_header = any(_norm_name(cell) in colmap or _norm_name(cell) in _ROW_HEADERS for cell in header)
    if not has_header:
        raise ValueError("붙여 넣은 표의 첫 줄에 CSV 열 이름을 넣어 주세요. 수정·삭제에는 row_number 열도 필요합니다.")
    data = raw[1:]
    if not data:
        raise ValueError("표 머리글 아래에 변경할 행이 없습니다.")
    if len(data) > MAX_ROWS:
        raise ValueError(f"한 번에 최대 {MAX_ROWS}행까지 변경할 수 있습니다.")
    return header, data


def _row_header(value: str) -> bool:
    return _norm_name(value) in _ROW_HEADERS


def _row_numbers_from_text(text: str) -> list[int]:
    found = []
    for match in re.finditer(r"(?:(\d+(?:\s*[,/]\s*\d+)+)|(\d+))\s*(?:번\s*)?행", text, re.I):
        found.extend(int(value) for value in re.findall(r"\d+", match.group(0)))
    en = re.search(r"\brows?\s+((?:\d+\s*[,/]\s*)*\d+)\b", text, re.I)
    if en:
        found.extend(int(value) for value in re.findall(r"\d+", en.group(1)))
    return list(dict.fromkeys(found))


def _single_update(text: str, columns: list[str]) -> tuple[int, str, str] | None:
    # Deterministic sentence form: "12행 category를 NEW로 수정".
    match = re.search(
        r"(?P<row>\d+)\s*(?:번\s*)?행\s+(?P<column>[A-Za-z0-9_가-힣 .-]+?)(?:을|를)?\s+"
        r"(?P<value>.+?)(?:으로|로)\s*(?:수정|변경)(?:해|해줘|해주세요)?[.!\s]*$", text, re.I)
    if not match:
        return None
    colmap = _column_map(columns)
    column = colmap.get(_norm_name(match.group("column")))
    if not column:
        raise ValueError(f"열 이름을 확인할 수 없습니다: {match.group('column').strip()}")
    return int(match.group("row")), column, match.group("value").strip()


def _ppid_order(rows: list[dict[str, str]], columns: list[str]) -> tuple[list[dict[str, str]], list[dict]]:
    from core.ppid_knob_order import normalize_ppid_knob_rule_order
    lookup = _column_map(columns)
    result = normalize_ppid_knob_rule_order(rows, schema={
        "feature_col": lookup.get("featurename", "feature_name"),
        "rule_order_col": lookup.get("ruleorder", "rule_order"),
        "operator_col": lookup.get("operator", "operator"),
        "value_col": lookup.get("value", "value"),
        "ppid_col": lookup.get("ppid", "ppid"),
        "category_col": lookup.get("category", "category"),
    })
    notices = [
        {**item, "message": (
            f"{item.get('feature_name', '')}/{item.get('ppid', '')}: "
            f"{item.get('from_rule_order', '')} → {item.get('to_rule_order', '')} ({item.get('reason', '')})"
        )}
        for item in result.get("changes") or []
    ]
    notices += [
        {**item, "message": (
            f"{item.get('feature_name', '')}/{item.get('ppid', '')} {item.get('rule_order', '')}: "
            f"{item.get('reason', '')}"
        )}
        for item in result.get("warnings") or []
    ]
    return list(result.get("rows") or []), notices


def _mutate(filename: str, action: str, text: str, table: dict | None,
            columns: list[str], rows: list[dict[str, str]]) -> tuple[list[dict[str, str]], list[dict], list[dict]]:
    original = [dict(row) for row in rows]
    final = [dict(row) for row in rows]
    affected: list[dict] = []
    notices: list[dict] = []
    colmap = _column_map(columns)

    if action == "update" and not table:
        one = _single_update(text, columns)
        if not one:
            raise ValueError("수정은 TSV 표에 row_number와 바꿀 열을 넣거나 ‘12행 category를 NEW로 수정’처럼 요청해 주세요.")
        row_number, column, value = one
        if row_number < 2 or row_number > len(final) + 1:
            raise ValueError(f"행 번호 {row_number}는 현재 CSV 줄 범위(2~{len(final) + 1}) 밖입니다.")
        before = dict(final[row_number - 2])
        final[row_number - 2][column] = value
        affected.append({"row_number": row_number, "before": before, "after": dict(final[row_number - 2])})
    elif action == "delete" and not table:
        numbers = _row_numbers_from_text(text)
        if not numbers:
            raise ValueError("삭제할 행 번호를 적어 주세요. 예: ‘12행 삭제’ 또는 row_number 열이 있는 TSV 표")
        for number in numbers:
            if number < 2 or number > len(final) + 1:
                raise ValueError(f"행 번호 {number}는 현재 CSV 줄 범위(2~{len(final) + 1}) 밖입니다.")
        for number in sorted(numbers, reverse=True):
            before = dict(final[number - 2])
            del final[number - 2]
            affected.append({"row_number": number, "before": before, "after": {}})
        affected.sort(key=lambda item: item["row_number"])
    else:
        header, data = _table_parts(text, table, action, columns)
        resolved: list[str | None] = []
        for cell in header:
            if _row_header(cell):
                resolved.append(None)
                continue
            actual = colmap.get(_norm_name(cell))
            if not actual:
                raise ValueError(f"{filename}에 없는 열입니다: {cell}")
            resolved.append(actual)
        row_index = next((index for index, cell in enumerate(header) if _row_header(cell)), None)
        if action in {"update", "delete"} and row_index is None:
            raise ValueError("수정·삭제 표에는 현재 조회 결과의 row_number(행번호) 열이 필요합니다.")
        if action == "add" and row_index is not None:
            raise ValueError("추가 표에는 row_number 열을 넣지 마세요. 새 행은 파일 끝에 추가됩니다.")
        seen_numbers: set[int] = set()
        for cells in data:
            if action == "add":
                new_row = {column: "" for column in columns}
                for index, actual in enumerate(resolved):
                    if actual is not None:
                        new_row[actual] = cells[index] if index < len(cells) else ""
                final.append(new_row)
                affected.append({"row_number": len(final) + 1, "before": {}, "after": dict(new_row)})
                continue
            raw_number = cells[row_index] if row_index < len(cells) else ""
            if not re.fullmatch(r"\d+", str(raw_number or "").strip()):
                raise ValueError(f"행 번호는 1 이상의 정수여야 합니다: {raw_number or '(빈 값)'}")
            number = int(raw_number)
            if number in seen_numbers:
                raise ValueError(f"같은 행 번호가 두 번 들어 있습니다: {number}")
            seen_numbers.add(number)
            if number < 2 or number > len(original) + 1:
                raise ValueError(f"행 번호 {number}는 현재 CSV 줄 범위(2~{len(original) + 1}) 밖입니다.")
            before = dict(original[number - 2])
            if action == "delete":
                affected.append({"row_number": number, "before": before, "after": {}})
            else:
                after = dict(before)
                for index, actual in enumerate(resolved):
                    if actual is not None:
                        after[actual] = cells[index] if index < len(cells) else ""
                final[number - 2] = after
                affected.append({"row_number": number, "before": before, "after": dict(after)})
        if action == "delete":
            for number in sorted(seen_numbers, reverse=True):
                del final[number - 2]

    if filename == "ppid_knob.csv":
        before_order = [dict(row) for row in final]
        final, notices = _ppid_order(final, columns)
        # Ordering may relabel additional rows.  Include every changed row in
        # the approval preview so apply never makes an invisible change.
        for index, (before, after) in enumerate(zip(before_order, final), start=2):
            if before == after:
                continue
            related = next((item for item in affected
                            if item["row_number"] == index and item.get("after")), None)
            if related is not None:
                related["after"] = dict(after)
                related["normalized"] = True
            else:
                affected.append({"row_number": index, "before": before, "after": dict(after), "normalized": True})
        affected.sort(key=lambda item: item["row_number"])
    return final, affected, notices


def _preview_rows(action: str, affected: list[dict], destinations: list[dict[str, str]],
                  columns: list[str]) -> tuple[list[dict], list[str]]:
    display = []
    for destination in destinations:
        for change in affected:
            row = {
                "저장 위치": destination["label"],
                "동작": {"add": "추가", "update": "수정", "delete": "삭제"}[action]
                + (" · 순서 정규화" if change.get("normalized") else ""),
                "row_number": change["row_number"],
            }
            for column in columns:
                row[f"이전.{column}"] = change["before"].get(column, "")
                row[f"변경.{column}"] = change["after"].get(column, "")
            display.append(row)
    out_columns = ["저장 위치", "동작", "row_number"]
    out_columns += [f"이전.{column}" for column in columns]
    out_columns += [f"변경.{column}" for column in columns]
    return display, out_columns


def _query(filename: str, text: str, context: dict):
    try:
        destinations = _destinations(filename)
        columns, rows = _source_document(destinations)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        return _reply(str(exc), context, ok=False)
    if not columns:
        return _reply(f"{filename} 파일을 찾을 수 없거나 머리글이 없습니다.", context, ok=False,
                      missing=["source_file"])
    context["pending_rulebook_file"] = filename
    requested = _row_numbers_from_text(text)
    if requested:
        selected = [(number, rows[number - 2]) for number in requested if 2 <= number <= len(rows) + 1]
    else:
        selected = list(enumerate(rows[:MAX_QUERY_ROWS], start=2))
    display = [{"row_number": number, **row} for number, row in selected]
    message = f"{filename} {len(rows)}행 중 {len(display)}행입니다."
    if not requested and len(rows) > MAX_QUERY_ROWS:
        message += f" 한 번에 {MAX_QUERY_ROWS}행까지만 표시했습니다. ‘{filename} 120행 조회’처럼 행 번호를 지정할 수 있습니다."
    message += (" 변경하려면 파일명과 동작을 분명히 적고 TSV를 붙여 주세요. "
                "수정·삭제는 먼저 조회한 row_number를 사용하며, 미리보기를 확인한 뒤 별도 승인해야 반영됩니다.")
    return _reply(message, context, table=display, columns=["row_number", *columns],
                  sources=[item["label"] for item in destinations], action="query", file=filename)


def _create_preview(filename: str, action: str, text: str, context: dict, user: dict,
                    table: dict | None):
    destinations = _destinations(filename)
    columns, rows = _source_document(destinations)
    if not columns:
        raise ValueError(f"DB의 {filename} 파일을 찾을 수 없거나 머리글이 없습니다. 저장 위치를 확인해 주세요.")
    final, affected, notices = _mutate(filename, action, text, table, columns, rows)
    if not affected or final == rows:
        raise ValueError("변경될 행이 없습니다.")
    _validate_changed_rows(filename, columns, affected)
    display, display_columns = _preview_rows(action, affected, destinations, columns)
    identifier = uuid.uuid4().hex
    proposal = {
        "id": identifier,
        "feature": FEATURE,
        "user": user["username"],
        "created": time.time(),
        "status": "pending",
        "file": filename,
        "action": action,
        "columns": columns,
        "final_rows": final,
        "affected": affected,
        "preview_rows": display,
        "preview_columns": display_columns,
        "order_notices": notices,
        "destinations": [
            {**item, "digest": _digest(Path(item["path"]))}
            for item in destinations
        ],
    }
    path = _proposal_path(identifier)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, proposal)
    context["pending_rulebook_update"] = identifier
    context["pending_rulebook_file"] = filename
    from core import audit
    audit.record_user(user["username"], "home:rulebook-preview",
                      detail=f"file={filename} action={action} rows={len(affected)}", tab="home")
    labels = [item["label"] for item in destinations]
    message = (f"{filename}의 영향을 받는 행을 아래와 같이 미리보기 했습니다. 저장 위치: {', '.join(labels)}. "
               "아직 저장하지 않았습니다. 내용을 확인한 뒤 승인 버튼을 눌러 주세요.")
    if notices:
        message += f" PPID 규칙 순서 점검 알림 {len(notices)}건도 미리보기에 반영했습니다."
    return _reply(message, context, table=display, columns=display_columns,
                  approval={"id": identifier, "status": "pending", "expires_in": TTL_SECONDS},
                  sources=labels, action=action, file=filename,
                  warnings=[str(item.get("message") or item) for item in notices])


def _backup(path: Path, identifier: str) -> str:
    if not path.is_file():
        return ""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    directory = PATHS.data_root / "rulebook_backups" / path.name
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{stamp}-{identifier[:8]}-{hashlib.sha256(str(path).encode()).hexdigest()[:8]}.csv"
    shutil.copy2(path, target)
    return str(target)


def _write_atomic(path: Path, payload: bytes, identifier: str) -> None:
    from core.file_transaction import replace_file
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{identifier[:8]}.tmp")
    try:
        temp.write_bytes(payload)
        replace_file(temp, path)
    finally:
        with contextlib.suppress(OSError):
            temp.unlink()


def _validate_destinations(proposal: dict) -> list[dict[str, str]]:
    expected = _destinations(str(proposal.get("file") or ""))
    proposed = proposal.get("destinations") or []
    if [(item["path"], item["label"]) for item in expected] != [
        (str(item.get("path") or ""), str(item.get("label") or "")) for item in proposed
    ]:
        raise ValueError("저장 위치가 미리보기 이후 바뀌었습니다. 현재 파일을 다시 조회하고 새 미리보기를 만들어 주세요.")
    return proposed


def _apply(proposal: dict, user: dict) -> tuple[list[str], list[dict]]:
    from contextlib import ExitStack
    from core.file_transaction import file_transaction
    destinations = _validate_destinations(proposal)
    paths = [Path(item["path"]) for item in destinations]
    ordered = sorted(paths, key=lambda item: os.path.normcase(str(item.absolute())))
    with ExitStack() as stack:
        for path in ordered:
            stack.enter_context(file_transaction(path, timeout=10.0))
        for item in destinations:
            if _digest(Path(item["path"])) != item.get("digest"):
                raise ValueError(
                    f"{Path(item['path']).name}이 미리보기 이후 다른 요청에서 변경되었습니다. "
                    "현재 내용을 다시 조회하고 변경을 새로 요청해 주세요."
                )
        rows = list(proposal.get("final_rows") or [])
        columns = list(proposal.get("columns") or [])
        if proposal.get("file") == "ppid_knob.csv":
            normalized, notices = _ppid_order(rows, columns)
            if normalized != rows:
                raise ValueError("PPID 규칙 순서가 미리보기와 달라졌습니다. 새 미리보기를 만들어 주세요.")
            proposal["order_notices_apply"] = notices
        payload = _csv_bytes(columns, rows)
        backups: list[str] = []
        original = {str(path): path.read_bytes() if path.is_file() else None for path in paths}
        written: list[Path] = []
        try:
            for path in paths:
                backup = _backup(path, proposal["id"])
                if backup:
                    backups.append(backup)
                _write_atomic(path, payload, proposal["id"])
                written.append(path)
        except Exception:
            for path in reversed(written):
                before = original[str(path)]
                if before is None:
                    with contextlib.suppress(OSError):
                        path.unlink()
                else:
                    with contextlib.suppress(Exception):
                        _write_atomic(path, before, proposal["id"] + "rollback")
            raise
    cache_results = []
    for path in paths:
        try:
            from core import matching_cache
            cache_results.append({"file": path.name, **matching_cache.refresh_matching_csv(path)})
        except Exception as exc:
            cache_results.append({"file": path.name, "ok": False, "error": type(exc).__name__})
    return backups, cache_results


def _decide(text: str, context: dict, user: dict, identifier: str):
    from core.file_transaction import file_transaction
    path = _proposal_path(identifier)
    with file_transaction(path, timeout=10.0):
        proposal = load_json(path, {})
        if not proposal or proposal.get("feature") != FEATURE or proposal.get("user") != user.get("username"):
            context.pop("pending_rulebook_update", None)
            raise ValueError("본인이 이 대화에서 요청한 매칭 파일 미리보기만 승인할 수 있습니다.")
        if not _can_edit(user, str(proposal.get("file") or "")):
            context.pop("pending_rulebook_update", None)
            raise ValueError("이 매칭 CSV를 관리하는 페이지의 관리자 권한이 필요합니다.")
        if proposal.get("status") == "applied":
            context.pop("pending_rulebook_update", None)
            return _reply("이미 반영된 요청입니다. 중복 저장하지 않았습니다.", context,
                          table=proposal.get("preview_rows") or [], columns=proposal.get("preview_columns") or [],
                          approval={"id": identifier, "status": "applied"},
                          sources=[item["label"] for item in proposal.get("destinations") or []],
                          action=proposal.get("action"), file=proposal.get("file"))
        if proposal.get("status") != "pending" or time.time() - float(proposal.get("created") or 0) > TTL_SECONDS:
            context.pop("pending_rulebook_update", None)
            raise ValueError("취소되었거나 만료된 매칭 파일 미리보기입니다. 현재 파일을 다시 조회해 요청해 주세요.")
        if _is_cancel(text):
            proposal["status"] = "cancelled"
            proposal["cancelled_at"] = time.time()
            save_json(path, proposal)
            context.pop("pending_rulebook_update", None)
            return _reply("매칭 파일 변경을 취소했습니다. 아무것도 저장하지 않았습니다.", context,
                          approval={"id": identifier, "status": "cancelled"},
                          sources=[item["label"] for item in proposal.get("destinations") or []])
        backups, cache_results = _apply(proposal, user)
        proposal.update(status="applied", applied_at=time.time(), backups=backups, cache_results=cache_results)
        save_json(path, proposal)
    context.pop("pending_rulebook_update", None)
    from core import audit
    audit.record_user(user["username"], "home:rulebook-save",
                      detail=f"file={proposal['file']} action={proposal['action']} rows={len(proposal['affected'])}", tab="home")
    message = (f"{proposal['file']}에 {len(proposal['affected'])}개 영향 행을 반영했습니다. "
               f"로컬 백업 {len(backups)}개를 남기고 매칭 캐시를 갱신했습니다.")
    failed_cache = [item for item in cache_results if not item.get("ok")]
    if failed_cache:
        message += " 파일 저장은 완료됐지만 일부 매칭 캐시 갱신에 실패했습니다. 다음 조회에서 다시 갱신됩니다."
    return _reply(message, context, table=proposal.get("preview_rows") or [],
                  columns=proposal.get("preview_columns") or [],
                  approval={"id": identifier, "status": "applied"},
                  sources=[item["label"] for item in proposal.get("destinations") or []],
                  action=proposal.get("action"), file=proposal.get("file"),
                  cache=cache_results, backups=len(backups))


def handle(text, context, request):
    """Handle supported CSV queries and two-turn edits; otherwise return None."""
    text = str(text or "").strip()
    pending = str(context.get("pending_rulebook_update") or "")
    tagged = _TAGGED.fullmatch(text)
    if tagged:
        if not pending:
            return None
        if tagged.group(2) != pending:
            return _reply("현재 매칭 파일 미리보기와 다른 승인 요청입니다. 최신 미리보기를 확인해 주세요.",
                          context, ok=False)
    decision = bool(tagged) or (bool(pending) and (_is_approval(text) or _is_cancel(text)))
    if decision:
        user = _user(request)
        if not user:
            context.pop("pending_rulebook_update", None)
            return _denied(context)
        try:
            return _decide(tagged.group(1) if tagged else text, context, user, pending)
        except (OSError, UnicodeError, csv.Error, ValueError, TimeoutError) as exc:
            return _reply(str(exc), context, ok=False)

    table = chat_table.parse(text)
    request_text = "\n".join(filter(None, [table.get("intro", ""), table.get("outro", "")])) if table else text
    selected_filename = _mentioned_file(request_text)
    if selected_filename is None:
        return _reply("한 번에 변경할 매칭 CSV 한 파일만 명시해 주세요.", context,
                      ok=False, missing=["one_file"])
    filename = selected_filename or str(context.get("pending_rulebook_file") or "")
    action = _action(request_text)
    waiting = str(context.get("pending_rulebook_request") or "")
    if waiting and selected_filename and not table and not action:
        text = waiting
        table = chat_table.parse(text)
        action = _action(text)
        filename = selected_filename
        context.pop("pending_rulebook_request", None)

    # A TSV with an explicit CSV action but no file gets a deterministic file
    # choice prompt.  Store it only in server conversation state.
    if table and action and not filename:
        if not re.search(r"csv|매칭\s*파일|룰북|rulebook", request_text, re.I):
            return None
        context["pending_rulebook_request"] = text
        return _reply("어느 매칭 CSV를 변경할지 파일명을 선택해 주세요.", context, ok=False,
                      missing=["file"], clarification={"kind": "file", "options": [
                          {"label": name, "value": name} for name in _FILES
                      ], "allow_other": False},
                      instructions="파일명을 선택하면 같은 TSV를 변경 미리보기로 다시 해석합니다.")
    if not filename:
        return None

    if pending:
        # Starting another operation abandons the conversational pointer.  The
        # old proposal stays persisted but cannot be approved from this chat.
        context.pop("pending_rulebook_update", None)

    if not action:
        if table:
            return _reply("동작을 분명히 적어 주세요: 추가(add), 수정(update), 삭제(delete). 아직 저장하지 않았습니다.",
                          context, ok=False, missing=["action"], file=filename)
        if _QUERY.search(text) or re.fullmatch(rf"\s*{re.escape(filename)}\s*", text, re.I):
            return _query(filename, text, context)
        return _reply(
            f"{filename}은 조회하거나 승인형 변경을 요청할 수 있습니다. 예: ‘{filename} 조회’, "
            f"‘{filename} 수정’ + row_number 열이 있는 TSV, ‘{filename} 12행 category를 NEW로 수정’, "
            "‘12행 삭제’. 수정·삭제 전에는 행 번호가 바뀌지 않도록 먼저 조회해 주세요.",
            context, ok=False, missing=["action_or_query"], file=filename)

    user = _user(request)
    if not _can_edit(user, filename):
        return _denied(context)
    try:
        return _create_preview(filename, action, text, context, user, table)
    except (OSError, UnicodeError, csv.Error, ValueError) as exc:
        return _reply(str(exc), context, ok=False, missing=["valid_csv_change"], file=filename)
