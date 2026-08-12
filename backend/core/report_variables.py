"""Template Report 변수 치환 — 저장된 ChartBuilder 코드를 랏마다 다시 쓰는 대신
``{{LOT}}`` 같은 토큰을 실행 시 값으로 바꿔 같은 템플릿을 여러 랏에 재사용한다.

문법은 두 가지뿐이다.
  ``{{LOT}}``        값을 그대로 끼워 넣는다.  SQL 쪽 따옴표는 템플릿이 갖는다.
                     예) ``WHERE root_lot_id = '{{LOT}}'``
  ``{{LOTS|list}}``  콤마로 나눈 값을 ``'A','B'`` 형태로 편다. IN 절용.
                     예) ``WHERE root_lot_id IN ({{LOTS|list}})``

값은 따옴표·세미콜론·주석 기호를 허용하지 않는다. SQL 은 아래 단계에서
``_validate_where_expression`` 이 다시 검사하지만, 문자열 리터럴 안에서
빠져나가는 입력은 여기서 먼저 막아야 검사기가 볼 수 있는 형태가 유지된다.
"""
from __future__ import annotations

import re

VARIABLE_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\|\s*([A-Za-z_]+)\s*)?\}\}")
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
# 값에 허용하는 문자 — 랏/제품/스텝/조건 이름과 숫자 범위 정도만 통과시킨다.
VALUE_RE = re.compile(r"^[A-Za-z0-9_.\-+/#:()\[\]가-힣ㄱ-ㅎㅏ-ㅣ, ]*$")
MAX_VALUE_LEN = 200
KNOWN_FILTERS = {"", "list", "upper", "lower"}


class ReportVariableError(ValueError):
    """치환할 수 없는 변수·값을 만났을 때."""


def normalize_name(value) -> str:
    name = str(value or "").strip()
    return name if NAME_RE.match(name) else ""


def extract_variables(*texts) -> list[str]:
    """등장 순서대로 중복 없이 변수 이름을 모은다."""
    found: list[str] = []
    for text in texts:
        for match in VARIABLE_RE.finditer(str(text or "")):
            name = match.group(1)
            if name not in found:
                found.append(name)
    return found


def validate_value(name: str, value) -> str:
    text = str(value if value is not None else "").strip()
    if len(text) > MAX_VALUE_LEN:
        raise ReportVariableError(f"{name} 값은 {MAX_VALUE_LEN}자 이하여야 합니다.")
    if not VALUE_RE.match(text):
        raise ReportVariableError(
            f"{name} 값에 쓸 수 없는 문자가 있습니다. 따옴표·세미콜론 없이 랏/제품 이름만 입력해 주세요."
        )
    return text


def split_list(value) -> list[str]:
    """콤마·줄바꿈으로 나눈 목록. 반복 실행과 ``|list`` 필터가 함께 쓴다."""
    return [item.strip() for item in re.split(r"[,\n]+", str(value or "")) if item.strip()]


def _render(name: str, filter_name: str, value: str) -> str:
    if filter_name == "list":
        items = split_list(value)
        if not items:
            raise ReportVariableError(f"{name} 목록이 비어 있습니다.")
        return ", ".join(f"'{item}'" for item in items)
    if filter_name == "upper":
        return value.upper()
    if filter_name == "lower":
        return value.lower()
    return value


def substitute(text, bindings: dict, *, strict: bool = True) -> str:
    """``{{NAME}}`` 을 bindings 값으로 바꾼다.

    strict 이면 값이 없는 변수에서 멈춘다 — 빈 값으로 조용히 치환하면
    ``root_lot_id = ''`` 처럼 "0행이 정상"인 조회가 만들어져 사람이 못 알아챈다.
    """
    raw = str(text or "")
    if not raw:
        return raw
    lookup = {str(key): value for key, value in (bindings or {}).items()}

    def replace(match: re.Match) -> str:
        name = match.group(1)
        filter_name = str(match.group(2) or "").casefold()
        if filter_name not in KNOWN_FILTERS:
            raise ReportVariableError(f"지원하지 않는 변수 필터입니다: {name}|{filter_name}")
        if name not in lookup or str(lookup[name] or "").strip() == "":
            if strict:
                raise ReportVariableError(f"변수 값을 입력해 주세요: {name}")
            return match.group(0)
        return _render(name, filter_name, validate_value(name, lookup[name]))

    return VARIABLE_RE.sub(replace, raw)


def validate_bindings(bindings: dict) -> dict[str, str]:
    """이름·값을 한 번에 검사한 사본을 돌려준다."""
    clean: dict[str, str] = {}
    for raw_name, raw_value in (bindings or {}).items():
        name = normalize_name(raw_name)
        if not name:
            raise ReportVariableError(f"변수 이름이 올바르지 않습니다: {raw_name}")
        clean[name] = validate_value(name, raw_value)
    return clean
