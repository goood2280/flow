"""Home chat: administrator alias updates for product semantics.

Two requests are understood:
- a table pasted from Excel (step_id · item_id · alias …) → Inline/ET item aliases
- a sentence such as "prodA 이름 프로드A도 인식하게 해줘" → product aliases

Nothing is written on the first turn. The server keeps the recognised change
as a proposal, shows it, and saves only after the same user approves it
("업데이트 해줘" or the approval button). Aliases are only ever added to what
is already registered; removal stays on the admin semantic tab. Every save is
written to the alias change log shown on that tab.
"""
from __future__ import annotations

import re
import time
import uuid

from core import chat_table
from core.paths import PATHS
from core.utils import load_json, save_json

FEATURE = "semantic.alias_update"
TTL_SECONDS = 1800
MAX_ENTRIES = 200
SOURCE_LABEL = "관리자 시맨틱 (제품 별칭 · Inline/ET 별칭)"
LOG_HINT = "관리자 > 제품 공정·시맨틱 > 별칭 변경 이력에서 확인할 수 있습니다."

_TAGGED = re.compile(r"(승인|취소)\s+([0-9a-f]{32})")
_DECISION_NOISE = re.compile(r"[\s.,!~?·]+")
_APPROVE = re.compile(
    r"(?:네|예|응|ㅇㅇ|ㅇㅋ|좋아요?|좋습니다|그래요?|그렇게|맞아요?|오케이|ok(?:ay)?|yes|확인|승인|진행|반영|적용|저장|"
    r"업데이트|update|추가|등록|넣어|해줘|해주세요|해|줘|주세요|하자|합니다|할게요?|하겠습니다|부탁해요?|부탁드립니다|요|ㄱㄱ)+", re.I)
_CANCEL = re.compile(r"(?:취소|아니|아니요|아니오|아뇨|no|하지마|하지마세요|하지말아줘|그만|안해|안할래|중단|보류|해줘|해|요|할게)+", re.I)
_NEGATIVE = re.compile(r"취소|아니|아뇨|\bno\b|하지\s*마|하지\s*말|그만|안\s*해|안\s*할|중단|보류", re.I)

_ALIAS_WORD = re.compile(r"별칭|별명|alias|시맨틱|시멘틱|semantic", re.I)
# "별칭 추가/등록", "…로도 인식하게", "…라고도 불러" — a write, not a lookup.
_ALIAS_INTENT = re.compile(
    r"(?:별칭|별명|alias)[^\n]{0,30}?(?:추가|등록|넣어|업데이트|update|저장)|(?:추가|등록|넣어)[^\n]{0,30}?(?:별칭|별명|alias)|"
    r"인식(?:하게|하도록|되게|되도록|시켜|해\s*줘|해\s*주세요)|알아\s*(?:듣|들)(?:게|도록)|"
    r"(?:이라고도|라고도|로도|으로도)\s*(?:불러|부를|부르|써|쓸|찾|인식)", re.I)

_FIELDS = {
    "product": ("product", "제품", "제품명", "vehicle"),
    "source_type": ("source", "sourcetype", "소스", "구분", "원천", "db", "type"),
    "module": ("module", "모듈"),
    "step_id": ("stepid", "step", "스텝", "스텝id", "stepno"),
    "step_desc": ("stepdesc", "stepdescription", "스텝설명", "스텝명", "공정명"),
    "item_id": ("itemid", "item", "아이템", "아이템id", "항목", "항목id", "측정항목"),
    "item_desc": ("itemdesc", "itemdescription", "아이템설명", "항목설명", "항목명"),
    "aliases": ("alias", "aliases", "별칭", "별명", "inline별칭", "et별칭", "이름", "name", "호칭", "용어",
                "신규별칭", "추가별칭"),
}
_FIELD_BY_NAME = {name: field for field, names in _FIELDS.items() for name in names}
_COLUMN_TOKEN = re.compile(r"[A-Za-z_]+(?:\s+id)?|[가-힣]+", re.I)
_COLUMN_GAP = re.compile(r"[\s,/|·>→\-]*")

_PARTICLES = sorted(("이라고도", "라고도", "이라고", "라고", "으로도", "로도", "으로", "이랑", "에게", "에서", "에도", "까지",
                     "처럼", "은", "는", "을", "를", "이", "가", "도", "와", "과", "의", "로", "랑", "에"), key=len, reverse=True)
_STOP_WORDS = {"이름", "별칭", "별명", "alias", "aliases", "제품", "제품명", "명칭", "호칭", "name", "이것", "이거", "그거",
               "것", "및", "또", "또는", "그리고", "같이", "새", "신규", "product", "해당", "앞으로", "이제", "지금", "그냥",
               "좀", "제발", "현재", "이제품", "그제품"}
_VERB_TOKEN = re.compile(
    r"해줘|해주세요|해주라|해라|하게|하도록|되게|되도록|시켜|주세요|싶어|싶다|바람|부탁|추가|등록|넣어|인식|알아|불러|부르|부를|"
    r"연결|매핑|업데이트|update|저장|반영|할래|합시다|하자|^해$|^줘$", re.I)
_QUOTED = re.compile(r"[\"'“”‘’「」『』`]([^\"'“”‘’「」『』`\n]{1,100})[\"'“”‘’「」『』`]")


def _norm(value) -> str:
    return re.sub(r"[\s_-]+", "", str(value or "")).casefold()


def _decision_text(text) -> str:
    return _DECISION_NOISE.sub("", str(text or ""))


def is_approval(text) -> bool:
    value = _decision_text(text)
    return bool(value) and len(value) <= 40 and bool(_APPROVE.fullmatch(value)) and not _NEGATIVE.search(text)


def is_cancel(text) -> bool:
    value = _decision_text(text)
    return bool(value) and len(value) <= 40 and bool(_CANCEL.fullmatch(value)) and bool(_NEGATIVE.search(text))


def _table_request(text):
    """A pasted table meant for aliases: its header has item_id and alias
    columns, or the instruction around it talks about aliases/semantics."""
    table = chat_table.parse(text)
    if not table:
        return None
    header = {_field(cell) for cell in table["rows"][0]}
    if {"item_id", "aliases"} <= header or _ALIAS_WORD.search(table["intro"] + " " + table["outro"]):
        return table
    return None


def _product_alias_request(text) -> bool:
    """Sentence form ("prodA 이름 프로드A도 인식하게 해줘"). The caller also
    requires the product to be named, or the word 제품."""
    if "\t" in text or "\n" in text.strip():
        return False
    return bool(_ALIAS_INTENT.search(text))


def is_candidate(text) -> bool:
    """Cheap check for the offload router: keep alias writes and short
    confirmations on the production API."""
    text = str(text or "").strip()
    return bool(_TAGGED.fullmatch(text) or is_approval(text) or is_cancel(text)
                or _table_request(text) or _product_alias_request(text))


# ── reply helpers ────────────────────────────────────────────────────────────

ITEM_PREVIEW_COLUMNS = ["No", "제품", "소스", "Step ID", "Item ID", "Item 설명", "기존 별칭", "추가 별칭", "결과"]
ITEM_RESULT_COLUMNS = ["제품", "소스", "Step ID", "Item ID", "Item 설명", "추가 별칭", "현재 별칭", "결과"]
PRODUCT_PREVIEW_COLUMNS = ["제품", "기존 별칭", "추가 별칭", "결과"]
PRODUCT_RESULT_COLUMNS = ["제품", "추가 별칭", "현재 별칭", "결과"]


def _reply(message, context, *, ok=True, table=None, columns=None, approval=None, **tool_fields):
    from core.data_chat import reply
    tool = {"feature": FEATURE, "sources": [SOURCE_LABEL], **tool_fields}
    if table is not None:
        tool["table"] = {"rows": table, "total": len(table)}
        if columns:
            present = {key for row in table for key in row}
            tool["table"]["columns"] = [column for column in columns if column in present]
    if approval:
        tool["approval"] = approval
    if context.get("product"):
        tool.setdefault("context", {"product": context["product"]})
    return reply(message, context=context, ok=ok, tool=tool,
                 interpretation={"summary": message, "product": context.get("product", ""), "source": SOURCE_LABEL})


def _user(request):
    if request is None:
        return None
    try:
        from core import auth
        return auth.current_user(request)
    except Exception:
        return None


def _can_manage(user) -> bool:
    if not user:
        return False
    if str(user.get("role") or "") == "admin":
        return True
    try:
        from core import auth
        return bool(auth.is_page_manager(user, "productwiki"))
    except Exception:
        return False


def _proposal_path(identifier):
    if not re.fullmatch(r"[0-9a-f]{32}", str(identifier or "")):
        raise ValueError("승인할 별칭 미리보기가 없습니다. 변경 내용을 먼저 요청해 주세요.")
    return PATHS.data_root / "chat_proposals" / f"semantic_{identifier}.json"


# ── table layout ─────────────────────────────────────────────────────────────

def _field(cell):
    return _FIELD_BY_NAME.get(re.sub(r"[\s_\-()\[\]./]+", "", str(cell or "")).casefold())


def _intro_order(intro):
    """Column order written in the instruction, e.g. "step id, item_id, alias"."""
    best, run, last_end = [], [], None
    for match in _COLUMN_TOKEN.finditer(intro or ""):
        field = _field(match[0])
        gap = intro[last_end:match.start()] if last_end is not None else ""
        if field and run and _COLUMN_GAP.fullmatch(gap) and field not in run:
            run.append(field)
        elif field:
            run = [field]
        else:
            run = []
        last_end = match.end()
        if len(run) > len(best):
            best = list(run)
    return best if "item_id" in best and len(best) >= 2 else []


def _layout(table, source):
    rows = table["rows"]
    width = len(rows[0])
    header = [_field(cell) for cell in rows[0]]
    named = [field for field in header if field]
    if len(named) >= 2 and len(set(named)) == len(named) and "item_id" in named:
        return header, rows[1:]
    order = _intro_order(table["intro"])
    if len(order) == width:
        return order, rows
    if width == 3:
        return ["step_id", "item_id", "aliases"], rows
    if width == 2 and source == "ET":
        return ["item_id", "aliases"], rows
    raise ValueError("표의 열을 확인하지 못했습니다. 첫 줄에 step_id · item_id · alias 머리글을 넣어 붙여 주세요."
                     " (ET는 item_id · alias 두 열도 됩니다)")


def _split_aliases(value):
    return [part.strip() for part in re.split(r"[,;|\n]+", str(value or "")) if part.strip()]


def _default_source(text):
    has_et = bool(re.search(r"(?<![A-Za-z0-9])ET(?![A-Za-z0-9])", text))
    has_inline = bool(re.search(r"inline|인라인", text, re.I))
    return "ET" if has_et and not has_inline else "INLINE"


# ── product resolution ───────────────────────────────────────────────────────

def _products():
    from core import data_chat
    return data_chat.available_product_names()


def _name_forms(value):
    """The spellings data_chat.product_candidates accepts as a product's own
    name (case, separators, ML_TABLE_/VH_ prefix, EVT0 vs 0) — no aliases."""
    clean = re.sub(r"^(?:ML_TABLE_|VH_)", "", str(value or ""), flags=re.I)
    plain = lambda item: re.sub(r"[^a-z0-9]", "", str(item).lower())
    return {form for item in (value, clean) for form in (plain(item), re.sub(r"evt(\d+)", r"\1", plain(item))) if form}


def _literal_products(text, products):
    """Products named in text by their own name (not through an alias)."""
    words = set()
    for word in re.findall(r"[A-Za-z][A-Za-z0-9_.-]*", str(text or "")):
        words |= _name_forms(word)
    return [product for product in products if _name_forms(product) & words]


def _resolve_product(text, context, products):
    """(product, error) — the product named in text, else the confirmed one.
    Quoted text is the alias being added, never the target product."""
    from core import data_chat
    text = _QUOTED.sub(" ", str(text or ""))
    literal = _literal_products(text, products)
    if len(literal) == 1:
        return literal[0], ""
    if len(literal) > 1:
        return "", "제품이 여러 개 적혀 있습니다: " + ", ".join(literal) + ". 한 번에 한 제품만 요청해 주세요."
    matches = data_chat.product_candidates(text, products)
    if len(matches) == 1:
        return matches[0], ""
    if len(matches) > 1:
        return "", "제품명이 여러 제품과 일치합니다: " + ", ".join(matches) + ". 실제 제품명으로 다시 요청해 주세요."
    confirmed = str(context.get("confirmed_product") or "")
    return (confirmed, "") if confirmed in products else ("", "")


def _ask_product(text, context, products):
    context["pending_semantic_request"] = text
    options = [{"label": name, "value": name} for name in products[:5]]
    return _reply("어느 제품의 별칭인지 선택해 주세요. 목록에 없으면 ‘기타 직접 입력’으로 제품명을 입력해 주세요.",
                  context, ok=False, missing=["product"],
                  clarification={"kind": "product", "options": options, "allow_other": True,
                                 "placeholder": "제품명을 입력하세요"},
                  table=[{"product": name} for name in products[:100]])


# ── previews ─────────────────────────────────────────────────────────────────

def _measurement_index(product):
    from core import product_semantics
    index = {}
    for row in product_semantics.overview(product)["measurements"]:
        source = str(row.get("source_type") or "").upper()
        if source not in product_semantics.SEMANTIC_SOURCES or not row.get("item_id"):
            continue
        step = "" if source == "ET" else str(row.get("step_id") or "")
        index.setdefault((source, step.casefold(), str(row["item_id"]).casefold()), row)
    return index


def _alias_targets(index):
    targets = {}
    for (source, _step, _item), row in index.items():
        for alias in row.get("aliases") or []:
            targets.setdefault(_norm(alias), []).append(row)
    return targets


def _item_preview(text, table, context, products, forced_product=""):
    from core import product_semantics
    source_default = _default_source(table["intro"] + " " + table["outro"])
    fields, data_rows = _layout(table, source_default)
    if not data_rows:
        raise ValueError("머리글 아래에 별칭을 넣을 행이 없습니다.")
    if len(data_rows) > MAX_ENTRIES:
        raise ValueError(f"한 번에 최대 {MAX_ENTRIES}행까지 반영합니다. 표를 나누어 붙여 주세요.")
    product = forced_product
    if not product:
        product, error = _resolve_product(table["intro"] + " " + table["outro"], context, products)
        if error:
            raise ValueError(error)
    if not product and "product" not in fields:
        return None, product
    indexes, targets, entries, display = {}, {}, {}, []
    for number, cells in enumerate(data_rows, start=1):
        row = {field: cells[i] if i < len(cells) else "" for i, field in enumerate(fields) if field}
        row_product = product
        if row.get("product"):
            from core import data_chat
            matches = data_chat.product_candidates(row["product"], products)
            row_product = matches[0] if len(matches) == 1 else ""
        source = (row.get("source_type") or source_default).strip().upper()
        source = "ET" if source == "ET" else "INLINE" if source in ("", "INLINE", "인라인") else source
        step, item = str(row.get("step_id") or "").strip(), str(row.get("item_id") or "").strip()
        new_aliases = _split_aliases(row.get("aliases"))
        shown = {"No": number, "제품": row_product or row.get("product", ""), "소스": source,
                 "Step ID": step, "Item ID": item, "추가 별칭": ", ".join(new_aliases)}
        status = ""
        if not row_product:
            status = "제품을 확인하지 못함 — 제외"
        elif source not in product_semantics.SEMANTIC_SOURCES:
            status = "소스는 INLINE 또는 ET만 가능 — 제외"
        elif not item or (source == "INLINE" and not step):
            status = ("Item ID 없음" if not item else "Step ID 없음") + " — 제외"
        elif not new_aliases:
            status = "별칭 없음 — 제외"
        elif any(len(alias) > 100 for alias in new_aliases):
            status = "별칭은 100자 이내 — 제외"
        if status:
            display.append({**shown, "결과": status})
            continue
        if row_product not in indexes:
            indexes[row_product] = _measurement_index(row_product)
        index = indexes[row_product]
        known = index.get((source, "" if source == "ET" else step.casefold(), item.casefold()))
        if not known:
            display.append({**shown, "결과": "매칭표에 없는 Step/Item 조합 — 제외 (관리자 탭에서 직접 추가)"})
            continue
        key = (row_product, source, known.get("step_id", "") if source == "INLINE" else "", known["item_id"])
        entry = entries.setdefault(key, {
            "product": row_product, "source_type": source, "step_id": key[2], "item_id": key[3],
            "module": known.get("module", ""), "step_desc": known.get("step_desc", ""),
            "item_desc": known.get("item_desc", ""), "existing": list(known.get("aliases") or []), "add": []})
        present = {_norm(value) for value in entry["existing"] + entry["add"]}
        added = [alias for alias in new_aliases if _norm(alias) not in present and _norm(alias) != _norm(key[3])]
        entry["add"].extend(added)
        if row_product not in targets:
            targets[row_product] = _alias_targets(index)
        conflicts = []
        for alias in added:
            for other in targets[row_product].get(_norm(alias), []):
                if (other.get("source_type"), other.get("step_id"), other.get("item_id")) != (source, key[2], key[3]):
                    conflicts.append(f"{alias}→{other.get('step_id') or '-'}/{other.get('item_id')}")
        shown.update({"Step ID": key[2], "Item ID": key[3], "Item 설명": entry["item_desc"],
                      "기존 별칭": ", ".join(entry["existing"]), "추가 별칭": ", ".join(added) or ", ".join(new_aliases)})
        if not added:
            shown["결과"] = "이미 등록됨 — 변경 없음"
        else:
            shown["결과"] = "추가 예정" + (" · 다른 항목에도 연결된 별칭: " + ", ".join(conflicts) if conflicts else "")
        display.append(shown)
    changes = [entry for entry in entries.values() if entry["add"]]
    return {"kind": "item", "product": product, "entries": changes, "rows": display}, product


def _alias_tokens(text, product):
    """Aliases named in a product alias sentence. Quoted text wins."""
    quoted = [value.strip() for value in _QUOTED.findall(text) if value.strip()]
    if quoted:
        tokens = quoted
    else:
        tokens = []
        for raw in re.split(r"[\s,;·、/]+", text):
            token = raw.strip(".!?~:()[]{}<>\"'")
            if not token or _VERB_TOKEN.search(token):
                continue
            for particle in _PARTICLES:
                rest = token[:-len(particle)]
                if token.endswith(particle) and rest and (len(rest) >= 2 or rest.isascii()):
                    token = rest
                    break
            if not token or token.casefold() in _STOP_WORDS or token in _PARTICLES:
                continue
            if _literal_products(token, [product]):
                continue
            tokens.append(token)
    out, seen = [], set()
    for token in tokens:
        if _norm(token) not in seen:
            seen.add(_norm(token))
            out.append(token)
    return out


def _product_preview(text, context, products, forced_product=""):
    from core import data_chat, product_semantics
    product = forced_product
    if not product:
        product, error = _resolve_product(text, context, products)
        if error:
            raise ValueError(error)
        if not product:
            return None, ""
    aliases = _alias_tokens(text, product)
    if not aliases:
        raise ValueError(f"{product}에 추가할 별칭을 찾지 못했습니다. 예: {product} 별칭 \"프로드A\" 추가해줘")
    registered = product_semantics.product_aliases()
    existing = next((list(row.get("aliases") or []) for row in registered
                     if product_semantics._key(row.get("product") or "") == product_semantics._key(product)), [])
    others = {}
    for row in registered:
        if product_semantics._key(row.get("product") or "") != product_semantics._key(product):
            for alias in row.get("aliases") or []:
                others.setdefault(_norm(alias), []).append(row.get("product"))
    names = {_norm(name): name for name in products}
    present = {_norm(value) for value in existing}
    add, display = [], []
    for alias in aliases:
        shown = {"제품": product, "추가 별칭": alias, "기존 별칭": ", ".join(existing)}
        other_product = [name for name in data_chat.product_candidates(alias, products) if name != product]
        if len(alias) < 2 or len(alias) > 100:
            shown["결과"] = "별칭은 2~100자 — 제외"
        elif _norm(alias) in names:
            shown["결과"] = f"실제 제품명({names[_norm(alias)]})과 같음 — 제외"
        elif _norm(alias) in present or _norm(alias) in {_norm(value) for value in add}:
            shown["결과"] = "이미 등록됨 — 변경 없음"
        elif other_product and _norm(alias) not in others:
            shown["결과"] = f"다른 제품({', '.join(other_product)})으로 인식되는 이름 — 제외"
        else:
            add.append(alias)
            shown["결과"] = "추가 예정" + (f" · 다른 제품({', '.join(others[_norm(alias)])})에도 등록된 별칭이라 이 이름으로 물으면 제품을 다시 묻습니다"
                                       if _norm(alias) in others else "")
        display.append(shown)
    entries = [{"product": product, "existing": existing, "add": add}] if add else []
    return {"kind": "product", "product": product, "entries": entries, "rows": display}, product


def _preview(text, context, request, user, forced_product=""):
    products = _products()
    table = _table_request(text)
    if table:
        proposal, product = _item_preview(text, table, context, products, forced_product)
    else:
        from core import product_semantics
        product, _error = _resolve_product(text, context, products)
        product = forced_product or product
        if product and not re.search(r"제품|이름|명칭", text):
            terms = [row for row in product_semantics.resolve_terms(product, text) if row.get("kind") == "measurements"]
            if terms:
                return _reply("Inline/ET 항목 별칭은 표로 붙여 주세요. 엑셀에서 step_id · item_id · alias 세 열을 복사해"
                              " ‘제품명 Inline 별칭 업데이트’와 함께 붙여 넣으면 됩니다.", context, ok=False,
                              missing=["alias_table"])
        proposal, product = _product_preview(text, context, products, forced_product)
    if proposal is None:
        return _ask_product(text, context, products)
    context.pop("pending_semantic_request", None)
    context.pop("pending_semantic_update", None)
    if product:
        context["product"] = product
    columns = PRODUCT_PREVIEW_COLUMNS if proposal["kind"] == "product" else ITEM_PREVIEW_COLUMNS
    if not proposal["entries"]:
        return _reply("아래와 같이 인식했지만 새로 추가할 별칭이 없습니다. 저장하지 않았습니다.", context, ok=False,
                      table=proposal["rows"], columns=columns)
    identifier = uuid.uuid4().hex
    proposal.update(id=identifier, user=user["username"], created=time.time(), status="pending", request=text[:4000])
    path = _proposal_path(identifier)
    path.parent.mkdir(parents=True, exist_ok=True)
    save_json(path, proposal)
    context["pending_semantic_update"] = identifier
    count = sum(len(entry["add"]) for entry in proposal["entries"])
    skipped = sum(1 for row in proposal["rows"] if not str(row.get("결과", "")).startswith("추가 예정"))
    if proposal["kind"] == "product":
        names = ", ".join(f"‘{alias}’" for alias in proposal["entries"][0]["add"])
        message = (f"아래와 같이 인식되었습니다. {product} 제품을 {names}(으)로도 인식하도록 제품 별칭을 추가할까요?"
                   " 아직 저장하지 않았습니다. 맞으면 ‘업데이트 해줘’ 또는 승인 버튼을 눌러 주세요.")
    else:
        sources = sorted({entry["source_type"] for entry in proposal["entries"]})
        message = (f"아래와 같이 인식되었습니다. {product or '표의 제품'} {'/'.join(sources)} 별칭 {count}개"
                   f"({len(proposal['entries'])}개 항목)를 추가할까요?" + (f" 제외·변경 없음 {skipped}행." if skipped else "") +
                   " 아직 저장하지 않았습니다. 맞으면 ‘업데이트 해줘’ 또는 승인 버튼을 눌러 주세요.")
    return _reply(message, context, table=proposal["rows"], columns=columns,
                  approval={"id": identifier, "status": "pending", "expires_in": TTL_SECONDS})


# ── apply ────────────────────────────────────────────────────────────────────

def _merge(existing, add):
    present = {_norm(value) for value in existing}
    return list(existing) + [alias for alias in add if _norm(alias) not in present]


def _apply_entry(kind, entry, actor):
    from core import product_semantics, product_wiki as wiki
    for _attempt in range(2):
        if kind == "product":
            current = next((row for row in product_semantics.product_aliases()
                            if product_semantics._key(row.get("product") or "") == product_semantics._key(entry["product"])), None)
        else:
            current = next((row for row in product_semantics.item_aliases(entry["product"], entry["source_type"])
                            if row.get("step_id") == entry["step_id"] and row.get("item_id") == entry["item_id"]), None)
        existing = list((current or {}).get("aliases") or [])
        merged = _merge(existing, entry["add"])
        if len(merged) == len(existing):
            return "이미 등록됨 — 변경 없음", existing
        try:
            if kind == "product":
                product_semantics.save_product_aliases(entry["product"], merged, actor,
                                                       (current or {}).get("updated_at", ""), via="chat")
            else:
                product_semantics.save_item_alias(
                    entry["product"], entry["step_id"], entry["item_id"], merged, actor,
                    entry.get("module", ""), entry.get("step_desc", ""), entry.get("item_desc", ""),
                    (current or {}).get("updated_at", ""), entry["source_type"], via="chat")
            return "추가됨", merged
        except wiki.Conflict:
            continue
    return "다른 관리자가 동시에 수정 중 — 다시 요청해 주세요", existing


def _decide(text, context, user, identifier):
    from core.file_transaction import file_transaction
    path = _proposal_path(identifier)
    with file_transaction(path):
        proposal = load_json(path, {})
        if not proposal or proposal.get("user") != user["username"]:
            context.pop("pending_semantic_update", None)
            raise ValueError("본인이 요청한 별칭 미리보기만 승인할 수 있습니다.")
        if proposal.get("status") == "applied":
            context.pop("pending_semantic_update", None)
            return _reply("이미 반영된 요청입니다. 중복 저장하지 않았습니다.", context, table=proposal.get("result_rows") or proposal["rows"],
                          approval={"id": identifier, "status": "applied"},
                          columns=PRODUCT_RESULT_COLUMNS if proposal["kind"] == "product" else ITEM_RESULT_COLUMNS)
        if proposal.get("status") != "pending" or time.time() - float(proposal.get("created") or 0) > TTL_SECONDS:
            context.pop("pending_semantic_update", None)
            raise ValueError("취소되었거나 만료된 별칭 미리보기입니다. 다시 요청해 주세요.")
        if is_cancel(text):
            proposal["status"] = "cancelled"
            save_json(path, proposal)
            context.pop("pending_semantic_update", None)
            return _reply("별칭 업데이트를 취소했습니다. 아무것도 저장하지 않았습니다.", context,
                          approval={"id": identifier, "status": "cancelled"})
        result_rows, saved = [], 0
        for entry in proposal["entries"]:
            status, aliases = _apply_entry(proposal["kind"], entry, user["username"])
            saved += status == "추가됨"
            row = {"제품": entry["product"]}
            if proposal["kind"] == "item":
                row.update({"소스": entry["source_type"], "Step ID": entry["step_id"], "Item ID": entry["item_id"],
                            "Item 설명": entry.get("item_desc", "")})
            row.update({"추가 별칭": ", ".join(entry["add"]), "현재 별칭": ", ".join(aliases), "결과": status})
            result_rows.append(row)
        proposal.update(status="applied", applied_at=time.time(), result_rows=result_rows)
        save_json(path, proposal)
    context.pop("pending_semantic_update", None)
    added = sum(len(entry["add"]) for entry, row in zip(proposal["entries"], result_rows) if row["결과"] == "추가됨")
    if proposal["kind"] == "product":
        message = f"{proposal['product']} 제품 별칭 {added}개를 추가했습니다. 이제 이 이름으로 물어도 {proposal['product']}로 인식합니다."
    else:
        message = f"Inline/ET 항목 {saved}개에 별칭 {added}개를 추가했습니다."
    if saved < len(result_rows):
        message += f" {len(result_rows) - saved}개는 반영하지 못했습니다. 결과 열을 확인해 주세요."
    return _reply(message + " " + LOG_HINT, context, ok=saved > 0 or all(row["결과"].startswith("이미") for row in result_rows),
                  table=result_rows, approval={"id": identifier, "status": "applied"},
                  columns=PRODUCT_RESULT_COLUMNS if proposal["kind"] == "product" else ITEM_RESULT_COLUMNS)


# ── entry point ──────────────────────────────────────────────────────────────

def _denied(context):
    return _reply("제품 별칭과 Inline/ET 별칭 변경은 관리자 또는 제품 위키 관리자만 할 수 있습니다.", context, ok=False,
                  blocked=True)


def _is_new_request(text):
    if _table_request(text):
        return True
    if not _product_alias_request(text):
        return False
    return bool(re.search(r"제품", text) or _literal_products(text, _products()))


def handle(text, context, request):
    """Return None when unrelated. Writes only after the same user approves."""
    text = str(text or "").strip()
    pending = context.get("pending_semantic_update")
    tagged = _TAGGED.fullmatch(text)
    if tagged and tagged[2] != pending:
        if pending or _proposal_path(tagged[2]).is_file():
            return _reply("현재 별칭 미리보기와 다른 승인 요청입니다. 최신 미리보기를 확인해 주세요.", context, ok=False)
        return None
    decision = bool(tagged) or (bool(pending) and (is_approval(text) or is_cancel(text)))
    if decision:
        user = _user(request)
        if not _can_manage(user):
            context.pop("pending_semantic_update", None)
            return _denied(context)
        try:
            return _decide(tagged[1] if tagged else text, context, user, pending)
        except ValueError as exc:
            return _reply(str(exc), context, ok=False)

    waiting = context.get("pending_semantic_request")
    new_request = _is_new_request(text)
    if waiting and not new_request:
        context.pop("pending_semantic_request", None)
        if is_cancel(text) or re.fullmatch(r"취소(?:해|해줘)?[.!\s]*", text):
            return _reply("별칭 요청을 취소했습니다. 아무것도 저장하지 않았습니다.", context)
        from core import data_chat
        matches = data_chat.product_candidates(text, _products())
        if len(matches) != 1:
            return None
        user = _user(request)
        if not _can_manage(user):
            return _denied(context)
        try:
            return _preview(waiting, context, request, user, forced_product=matches[0])
        except ValueError as exc:
            return _reply(str(exc), context, ok=False)
    if not new_request:
        if pending:
            # The user moved on without deciding; the preview is abandoned.
            context.pop("pending_semantic_update", None)
        return None
    user = _user(request)
    if not _can_manage(user):
        return _denied(context)
    try:
        return _preview(text, context, request, user)
    except ValueError as exc:
        return _reply(str(exc), context, ok=False, missing=["alias_request"])
