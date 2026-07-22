"""core/vehicle_reformatter.py — auto report 방식 vehicle reformatter CSV 실행 엔진.

auto report 의 `reformatter/{vehicle}_reformatter.csv` 를 그대로 읽어
DB ET(long: item_id/value) 데이터를 shot 단위 wide 로 pivot 한 뒤
REAL(abs/scale) → ADDP(수식) 순서로 index 컬럼을 계산한다.

CSV 스키마 (auto report 와 동일):
  num, CATEGORY(REAL|ADDP), ITEMID, ALIAS, ABSOLUTE, SCALE FACTOR, ADDP FORM,
  UNIT, SPECLOW, SPECHIGH, TARGET, REPORT ORDER, ...

ADDP FORM 은 `{ALIAS}` 로 다른 컬럼(REAL alias 또는 다른 ADDP)을 참조하며,
ADDP → ADDP 재귀 참조는 auto report 처럼 고정점 반복으로 해소한다.
수식 평가는 core/reformatter 의 안전 평가기(_eval_safe_expr)를 재사용한다
(임의 코드 실행 없음). auto report 의 rmax/rmin 은 행단위 max/min 으로 매핑.
"""
from __future__ import annotations

import ast
import importlib.util
import logging
import math
import re
from pathlib import Path
from typing import Any, Dict, List

import polars as pl

from core.paths import PATHS
from core.reformatter import _ALLOWED_AST_NODES, _SAFE_FUNCS, _bool_value, _sanitize_expr_name

logger = logging.getLogger("flow.vehicle_reformatter")

# shot 단위 pivot 의 key 후보 (존재하는 것만 사용)
PIVOT_KEY_COLS = [
    "product", "root_lot_id", "lot_id", "wafer_id",
    "step_id", "step_seq", "flat", "flat_zone", "shot_x", "shot_y",
]
# key 는 아니지만 결과에 같이 실어줄 메타 (shot 그룹 내 first)
# total_site_cnt 는 출력 컬럼에서 제외 — PGM(pt) 라벨이 site 수 정보를 담고 있고,
# 필터는 raw(long) 단계(routers/reformatize._apply_filters)에서 적용되므로 무관.
PIVOT_META_COLS = ["tkin_time", "tkout_time", "eqp_id", "chamber_id", "pgm"]

_PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")
# auto report 전용 함수명 → 표준 이름 매핑 (수식 텍스트 치환)
_FUNC_ALIASES = {"rmax": "max", "rmin": "min", "ABS": "abs", "POWER": "power", "AVG": "avg", "STD": "std", "stddev": "std"}
# auto report 의 wafer 단위 groupby transform (std/AVG) 기준 키
GROUP_TRANSFORM_KEYS = ["root_lot_id", "wafer_id", "tkout_time"]


# ── ADDP 수식 평가 — core/reformatter 의 AST 화이트리스트를 재사용하되
#    auto report 호환 함수(log10/LOG/power/std/avg)를 추가한 확장 세트 ──
def _f_log10(*args):
    """LOG(base, x) — auto report 는 첫 인자(base)를 무시하고 log10(abs(x)). 단일 인자도 허용."""
    x = args[-1]
    v = x.abs() if isinstance(x, pl.Expr) else abs(x)
    return v.log(10) if isinstance(v, pl.Expr) else math.log10(v)


def _f_power(a, b):
    return a ** b


def _vehicle_funcs(group_keys: list[str]) -> dict:
    def _over(expr, agg):
        e = expr if isinstance(expr, pl.Expr) else pl.lit(expr, dtype=pl.Float64)
        e = getattr(e, agg)()
        return e.over(group_keys) if group_keys else e

    funcs = {k: v for k, v in _SAFE_FUNCS.items() if k != "manual"}
    funcs.update({
        "log10": _f_log10,
        "LOG": _f_log10,
        "power": _f_power,
        "std": lambda x: _over(x, "std"),
        "avg": lambda x: _over(x, "mean"),
    })
    return funcs


def _compile_expr(expr: str, names: set[str], funcs: dict[str, Any]):
    """AST 화이트리스트 검증 후 code object 반환 (임의 코드 실행 차단)."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise ValueError(f"수식 문법 오류: {e}") from e
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_AST_NODES):
            raise ValueError(f"지원하지 않는 구문: {type(node).__name__}")
        if isinstance(node, ast.Name):
            if node.id not in names and node.id not in funcs:
                raise ValueError(f"알 수 없는 변수/함수: {node.id}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in funcs:
                fn = getattr(getattr(node, "func", None), "id", "?")
                raise ValueError(f"지원하지 않는 함수: {fn}")
    return compile(tree, "<vehicle-addp-expr>", "eval")


def _eval_vehicle_expr(expr: str, env: dict[str, Any], group_keys: list[str]):
    funcs = _vehicle_funcs(group_keys)
    code = _compile_expr(expr, set(env.keys()), funcs)
    return eval(code, {"__builtins__": {}}, {**funcs, **env})  # noqa: S307


# ── row 단위 매뉴얼 함수 — auto report MA_Window 내장 + manual_functions.py 훅 ──
# 관리자가 data_root/reformatter/manual_functions.py 에 파이썬 함수를 정의하면
# ADDP Form 에서 함수명으로 바로 호출 가능 (mtime 변경 감지로 저장 즉시 반영).
# 이 함수들은 polars Expr 로 표현할 수 없어 행 단위(map_elements)로 평가된다.
def _to_float(v):
    try:
        if v is None or v == "":
            return None
        out = float(v)
        return out if math.isfinite(out) else None
    except Exception:
        return None


def _poly2_fit(xs: list[float], ys: list[float]) -> tuple[float, float, float]:
    """최소자승 y = a2*x^2 + a1*x + a0 (정규방정식, numpy 불필요)."""
    n = len(xs)
    sx = sum(xs); sx2 = sum(x * x for x in xs)
    sx3 = sum(x ** 3 for x in xs); sx4 = sum(x ** 4 for x in xs)
    sy = sum(ys); sxy = sum(x * y for x, y in zip(xs, ys))
    sx2y = sum(x * x * y for x, y in zip(xs, ys))
    m = [[sx4, sx3, sx2, sx2y], [sx3, sx2, sx, sxy], [sx2, sx, n, sy]]
    for i in range(3):
        piv = max(range(i, 3), key=lambda r: abs(m[r][i]))
        m[i], m[piv] = m[piv], m[i]
        if abs(m[i][i]) < 1e-12:
            raise ValueError("singular fit")
        fac = m[i][i]
        for j in range(4):
            m[i][j] /= fac
        for k in range(3):
            if k != i:
                f = m[k][i]
                for j in range(4):
                    m[k][j] -= f * m[i][j]
    return m[0][3], m[1][3], m[2][3]   # a2, a1, a0


def _ma_window_calc(*args) -> dict[str, float | None]:
    """auto report My_Function.MA_Window 의 행 단위 이식.

    사용: MA_Window([x1..xN], y1..yN, spec, compliance=10)
      - y 는 log10(y + 1e-14) 변환 후 2차식 fitting
      - spec 은 log10(spec) 레벨과의 교점(margin) 계산
    반환 dict: minus_margin / plus_margin / ovl_index / window / new
    """
    xs = None
    scalars: list[float | None] = []
    for a in args:
        if isinstance(a, (list, tuple)):
            xs = [_to_float(v) for v in a]
        else:
            scalars.append(_to_float(a))
    if not xs or any(v is None for v in xs):
        raise ValueError("MA_Window 는 첫 인자로 x 좌표 리스트가 필요합니다 (예: [0,1,2])")
    n = len(xs)
    if len(scalars) < n + 1:
        raise ValueError(f"MA_Window 인자 부족 — y {n}개 + spec (+compliance) 필요")
    ys_raw = scalars[:n]
    spec = scalars[n]
    compliance = abs(scalars[n + 1]) if len(scalars) > n + 1 and scalars[n + 1] is not None else 10.0
    if spec is None or spec <= 0 or any(v is None for v in ys_raw):
        return {"minus_margin": None, "plus_margin": None, "ovl_index": None, "window": None, "new": None}
    spec_log = math.log10(spec)
    ys = [math.log10(v + 1e-14) for v in ys_raw]
    try:
        a2, a1, a0 = _poly2_fit(xs, ys)
    except Exception:
        return {"minus_margin": None, "plus_margin": None, "ovl_index": None, "window": None, "new": None}
    disc = a1 * a1 - 4 * a2 * (a0 - spec_log)
    if abs(a2) < 1e-12:
        plus = minus = None
    elif disc >= 0:
        s = math.sqrt(disc)
        plus = (-a1 + s) / (2 * a2)
        minus = (-a1 - s) / (2 * a2)
        plus = min(plus, compliance)
        minus = max(minus, -compliance)
    elif a2 > 0:
        plus = minus = 0.0            # 볼록·교점 없음
    else:
        plus, minus = compliance, -compliance   # 오목 예외
    if a2 <= 0 and abs(a2) >= 1e-12:
        plus, minus = compliance, -compliance
    if plus is None or minus is None:
        return {"minus_margin": None, "plus_margin": None, "ovl_index": None, "window": None, "new": None}
    return {
        "minus_margin": minus,
        "plus_margin": plus,
        "ovl_index": -0.5 * (plus + minus),
        "window": plus - minus,
        "new": min(abs(plus), abs(minus)),
    }


def _f_ma_window(*args):
    """MA_Window(...) → window 폭 (plus_margin - minus_margin). auto report 의 '' 컬럼."""
    return _ma_window_calc(*args)["window"]


def _f_ma_ovl_index(*args):
    """MA_OVL_INDEX(...) → 최적 중심 보정치 (-0.5*(plus+minus)). auto report 의 ovl_index."""
    return _ma_window_calc(*args)["ovl_index"]


def _f_ma_window_min(*args):
    """MA_WINDOW_MIN(...) → min(|plus|,|minus|). auto report 의 'new' window."""
    return _ma_window_calc(*args)["new"]


_BUILTIN_ROWWISE = {
    "MA_Window": _f_ma_window,
    "MA_OVL_INDEX": _f_ma_ovl_index,
    "MA_WINDOW_MIN": _f_ma_window_min,
}

_MANUAL_FILE = PATHS.data_root / "reformatter" / "manual_functions.py"
_MANUAL_CACHE: dict[str, Any] = {"sig": (), "funcs": {}}


def manual_functions() -> dict[str, Any]:
    """manual_functions.py 의 공개 함수들 (mtime 변경 시 자동 재로딩)."""
    try:
        st = _MANUAL_FILE.stat()
        sig = (str(_MANUAL_FILE), st.st_mtime, st.st_size)
    except OSError:
        sig = None
    if _MANUAL_CACHE["sig"] == sig:
        return _MANUAL_CACHE["funcs"]
    funcs: dict[str, Any] = {}
    if sig is not None:
        try:
            spec = importlib.util.spec_from_file_location("flow_vehicle_manual", str(_MANUAL_FILE))
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            for name in dir(mod):
                if name.startswith("_"):
                    continue
                fn = getattr(mod, name)
                if callable(fn) and getattr(fn, "__module__", "") == "flow_vehicle_manual":
                    funcs[name] = fn
        except Exception as e:
            logger.warning("manual_functions.py 로딩 실패: %s", e)
    _MANUAL_CACHE["sig"] = sig
    _MANUAL_CACHE["funcs"] = funcs
    return funcs


def rowwise_functions() -> dict[str, Any]:
    return {**_BUILTIN_ROWWISE, **manual_functions()}


def rowwise_function_help() -> list[dict[str, str]]:
    """수식 도움말용 — 내장 MA_* + manual_functions.py 함수 목록."""
    out = [
        {"name": "MA_Window([x...], y1..yN, spec, compliance)", "desc": "2차식 fitting window 폭 (auto report MA_Window). y 는 log10 변환 후 spec 교점 margin", "kind": "builtin"},
        {"name": "MA_OVL_INDEX([x...], y1..yN, spec, compliance)", "desc": "fitting 최적 중심 보정치 (-0.5*(plus+minus))", "kind": "builtin"},
        {"name": "MA_WINDOW_MIN([x...], y1..yN, spec, compliance)", "desc": "min(|plus_margin|, |minus_margin|)", "kind": "builtin"},
    ]
    for name, fn in manual_functions().items():
        doc = (fn.__doc__ or "").strip().splitlines()
        out.append({"name": f"{name}(...)", "desc": doc[0] if doc else "manual_functions.py 정의 함수", "kind": "manual"})
    return out


def _scalar_where(cond, a, b):
    return a if cond else b


def _rowwise_scalar_funcs() -> dict[str, Any]:
    """row 단위 평가에서 쓰는 스칼라 함수 세트 — Expr 전용(std/avg/where)은 스칼라 대체/제외."""
    funcs = {k: v for k, v in _SAFE_FUNCS.items() if k not in ("manual", "where")}
    funcs.update({"log10": _f_log10, "LOG": _f_log10, "power": _f_power, "where": _scalar_where})
    return funcs


FORMULA_HELP = [
    {"name": "abs(x)", "desc": "절대값"},
    {"name": "sqrt(x)", "desc": "제곱근"},
    {"name": "log(x) / log10(x) / LOG(base, x)", "desc": "자연로그 / 상용로그 (LOG 는 auto report 호환 — log10(abs(x)))"},
    {"name": "exp(x)", "desc": "지수"},
    {"name": "power(a, b) / a ** b", "desc": "거듭제곱"},
    {"name": "round(x, n)", "desc": "반올림"},
    {"name": "clip(x, lo, hi)", "desc": "범위 제한"},
    {"name": "min(a, b, ...) / rmin(...)", "desc": "행단위 최소"},
    {"name": "max(a, b, ...) / rmax(...)", "desc": "행단위 최대"},
    {"name": "where(cond, a, b)", "desc": "조건 선택 (예: where({A} > 0, {A}, 0))"},
    {"name": "std(x) / avg(x)", "desc": "wafer 단위(root_lot·wafer·tkout_time) 산포/평균 — auto report 의 STD/AVG"},
]


def resolve_needed_items(table: List[Dict[str, Any]], selected_aliases: List[str],
                         ) -> tuple[List[Dict[str, Any]], set[str]]:
    """선택된 alias 에서 ADDP 의존성을 재귀 해소해 필요한 테이블 행과 ITEMID 집합을 반환.

    ADDP 수식이 {이름} 으로 다른 alias 를 참조할 때 해당 alias 의 REAL ITEMID 까지
    추적하여 raw 데이터 필터링에 사용할 ITEMID 세트를 만든다.
    """
    by_alias: dict[str, dict] = {r["alias"]: r for r in table}
    needed_aliases: set[str] = set()
    extra_itemids: set[str] = set()           # 테이블에 없는 직접 참조 ITEMID
    queue = list(selected_aliases)
    while queue:
        name = queue.pop()
        if name in needed_aliases:
            continue
        row = by_alias.get(name)
        if row:
            needed_aliases.add(name)
            if row["category"] == "addp":
                refs = formula_refs(row.get("addp_form") or "")
                for ref in refs:
                    if ref not in needed_aliases:
                        queue.append(ref)
        else:
            # 테이블에 없으면 raw ITEMID 직접 참조로 간주
            extra_itemids.add(name)
    needed_rows = [r for r in table if r["alias"] in needed_aliases]
    needed_itemids = {r["itemid"] for r in needed_rows
                      if r["category"] == "real" and r["itemid"]}
    needed_itemids |= extra_itemids
    return needed_rows, needed_itemids


def _num_or_none(value):
    try:
        if value is None or str(value).strip() == "":
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except Exception:
        return None


def load_vehicle_table(csv_path: Path) -> List[Dict[str, Any]]:
    """vehicle_*_reformatter.csv → 정규화된 행 목록.

    반환 행 키: num, category(real|addp), itemid, alias, absolute, scale,
    addp_form, unit, speclow, spechigh, target, report_order, cat1, cat2
    """
    df = pl.read_csv(str(csv_path), infer_schema_length=0, try_parse_dates=False)
    by_lower = {str(c).strip().lower(): c for c in df.columns}

    def col(row: dict, *names: str) -> str:
        for n in names:
            c = by_lower.get(n)
            if c is not None and row.get(c) is not None:
                return str(row.get(c))
        return ""

    rows: list[dict[str, Any]] = []
    for raw in df.to_dicts():
        category = col(raw, "category").strip().upper()
        alias = col(raw, "alias").strip()
        itemid = col(raw, "itemid", "item_id").strip()
        addp_form = col(raw, "addp form", "addp_form").strip()
        if category not in ("REAL", "ADDP") or not alias:
            continue
        if category == "REAL" and not itemid:
            continue
        if category == "ADDP" and not addp_form:
            continue
        scale = _num_or_none(col(raw, "scale factor", "scale_factor"))
        rows.append({
            "num": col(raw, "num", "no").strip(),
            "category": category.lower(),
            "itemid": itemid,
            "alias": alias,
            "absolute": _bool_value(col(raw, "absolute", "abs"), False),
            "scale": scale if scale is not None else 1.0,
            "addp_form": addp_form,
            "unit": col(raw, "unit").strip(),
            "speclow": _num_or_none(col(raw, "speclow")),
            "spechigh": _num_or_none(col(raw, "spechigh")),
            "target": _num_or_none(col(raw, "target")),
            "report_order": _num_or_none(col(raw, "report order", "report_order")),
            "cat1": col(raw, "cat1", "report_cat1").strip(),
            "cat2": col(raw, "cat2", "report_cat2").strip(),
        })
    return rows


def find_vehicle_csv(base_dir: Path, product: str) -> Path | None:
    """제품명에 맞는 `<vehicle>_reformatter.csv` 를 찾는다.

    1) `<product>_reformatter.csv` 정확 일치 (대소문자 무시)
    2) vehicle 토큰이 product 를 포함하거나 product 가 vehicle 을 포함
       (예: VH_PRODA_reformatter.csv ↔ PRODA)
    """
    if not base_dir.is_dir() or not product:
        return None
    prod_cf = product.strip().casefold()
    candidates = sorted(
        p for p in base_dir.iterdir()
        if p.is_file() and p.name.casefold().endswith("_reformatter.csv")
    )
    for p in candidates:
        vehicle = p.name[: -len("_reformatter.csv")].casefold()
        if vehicle == prod_cf:
            return p
    for p in candidates:
        vehicle = p.name[: -len("_reformatter.csv")].casefold()
        if prod_cf in vehicle or vehicle in prod_cf:
            return p
    return None


def _normalize_form(addp_form: str) -> tuple[str, dict[str, str], list[str]]:
    """`{ALIAS}` placeholder → 안전한 식별자 토큰으로 치환.

    반환: (치환된 수식, {토큰: 원본 컬럼명}, 참조 컬럼 목록)
    """
    expr = str(addp_form or "").strip()
    for src, dst in _FUNC_ALIASES.items():
        expr = re.sub(rf"\b{src}\s*\(", dst + "(", expr)
    refs: list[str] = []
    for raw in _PLACEHOLDER_RE.findall(expr):
        name = raw.strip()
        if name and name not in refs:
            refs.append(name)
    token_map: dict[str, str] = {}
    used: set[str] = set()
    for name in refs:
        base = _sanitize_expr_name(name)
        token = base
        i = 2
        while token in used:
            token = f"{base}_{i}"
            i += 1
        used.add(token)
        token_map[token] = name
        expr = expr.replace("{" + name + "}", token)
    return expr, token_map, refs


def formula_refs(addp_form: str) -> list[str]:
    """ADDP Form 이 `{이름}` 으로 참조하는 컬럼 목록 (표시/문서화용)."""
    return _normalize_form(addp_form)[2]


def reformatize(df: pl.DataFrame, table: List[Dict[str, Any]],
                item_col: str = "item_id", value_col: str = "value",
                selected_aliases: list[str] | None = None,
                ) -> tuple[pl.DataFrame, list[str], list[str]]:
    """long ET df + vehicle 테이블 → (full wide df, 출력 컬럼 순서, 에러 목록).

    full wide 는 raw item 컬럼을 포함한다 (관리자 수식 테스트에서 참조 가능).
    출력 컬럼 순서: pivot key → 메타 → alias(REPORT ORDER 순).

    ``selected_aliases`` 가 주어지면 ADDP 의존성을 재귀 해소한 뒤 필요한
    ITEMID 만 raw 데이터에서 필터하여 pivot 한다. 불필요한 컬럼을 줄여
    대규모 ET 데이터에서 10~30× 속도 개선.
    """
    errors: list[str] = []
    if item_col not in df.columns or value_col not in df.columns:
        raise ValueError(f"ET 데이터에 {item_col}/{value_col} 컬럼이 없습니다")
    keys = [c for c in PIVOT_KEY_COLS if c in df.columns]
    metas = [c for c in PIVOT_META_COLS if c in df.columns]
    if not keys:
        raise ValueError("pivot key 컬럼이 없습니다 (root_lot_id/wafer_id/shot_x ...)")

    # ── 메타 컬럼은 item_id 필터 전에 추출 (메타는 shot 당 공통) ──
    meta_df = None
    if metas:
        meta_df = df.group_by(keys).agg([pl.col(m).first() for m in metas])

    # ── 선택된 alias 가 있으면 필요한 ITEMID 만 필터 → pivot 속도 개선 ──
    work_table = table
    if selected_aliases:
        work_table, needed_ids = resolve_needed_items(table, selected_aliases)
        if needed_ids and item_col in df.columns:
            df = df.filter(pl.col(item_col).is_in(list(needed_ids)))

    wide = (
        df.group_by(keys + [item_col])
        .agg(pl.col(value_col).cast(pl.Float64, strict=False).mean().alias("_v"))
        .pivot(index=keys, on=item_col, values="_v", aggregate_function="first")
    )
    if meta_df is not None:
        wide = wide.join(meta_df, on=keys, how="left")

    # ── REAL: alias = (item * scale) [abs] — 일괄 처리 ──
    real_exprs: list[pl.Expr] = []
    real_null_aliases: list[str] = []
    for row in work_table:
        if row["category"] != "real":
            continue
        alias, itemid = row["alias"], row["itemid"]
        if itemid not in wide.columns:
            errors.append(f"REAL '{alias}': ITEMID '{itemid}' 가 ET 데이터에 없습니다")
            real_null_aliases.append(alias)
            continue
        expr = pl.col(itemid).cast(pl.Float64, strict=False) * float(row["scale"])
        if row["absolute"]:
            expr = expr.abs()
        real_exprs.append(expr.alias(alias))
    if real_null_aliases:
        wide = wide.with_columns([pl.lit(None, dtype=pl.Float64).alias(a)
                                   for a in real_null_aliases])
    if real_exprs:
        wide = wide.with_columns(real_exprs)

    # ── ADDP: 고정점 반복 (ADDP → ADDP 재귀 참조 해소) ──
    wide, addp_errors = apply_addp_rows(wide, [r for r in work_table if r["category"] == "addp"])
    errors.extend(addp_errors)

    ordered = sorted(
        work_table,
        key=lambda r: (r["report_order"] if r["report_order"] is not None else 9999,
                       str(r["alias"])),
    )
    aliases = [r["alias"] for r in ordered if r["alias"] in wide.columns]
    out_cols, seen = [], set()
    for c in keys + metas + aliases:
        if c in wide.columns and c not in seen:
            out_cols.append(c)
            seen.add(c)
    # raw item 컬럼까지 포함한 full wide 를 유지 (관리자 수식 테스트가 raw item 참조 가능).
    wide = wide.sort(keys)
    return wide, out_cols, errors


def apply_addp_rows(wide: pl.DataFrame, rows: List[Dict[str, Any]]) -> tuple[pl.DataFrame, list[str]]:
    """ADDP 행들을 고정점 반복으로 적용. rows 는 {alias, addp_form} 필수.

    auto report 의 Reformatize 와 동일하게 계산 가능한 항목부터 매 패스 해소하고,
    끝까지 참조가 풀리지 않으면 null 컬럼 + 에러 메시지를 남긴다.
    """
    errors: list[str] = []
    group_keys = [c for c in GROUP_TRANSFORM_KEYS if c in wide.columns]
    pending = [r for r in rows if str(r.get("alias") or "").strip()]
    max_passes = max(10, len(pending) + 1)
    for _ in range(max_passes):
        if not pending:
            break
        progressed = False
        for row in list(pending):
            alias = str(row["alias"]).strip()
            expr_norm, token_map, refs = _normalize_form(row.get("addp_form") or "")
            if not expr_norm:
                errors.append(f"ADDP '{alias}': 수식이 비어 있습니다")
                wide = wide.with_columns(pl.lit(None, dtype=pl.Float64).alias(alias))
                pending.remove(row)
                progressed = True
                continue
            if any(r not in wide.columns for r in refs):
                continue  # 참조 컬럼이 아직 없음 → 다음 패스에서 재시도
            rowwise = rowwise_functions()
            used_rowwise = [n for n in rowwise if re.search(rf"\b{re.escape(n)}\s*\(", expr_norm)]
            try:
                if used_rowwise:
                    # MA_Window / manual 함수 — polars Expr 불가, 행 단위 평가.
                    funcs_row = {**_rowwise_scalar_funcs(), **rowwise}
                    code = _compile_expr(expr_norm, set(token_map.keys()), funcs_row)
                    needed = {t: c for t, c in token_map.items()}
                    struct_cols = sorted(set(needed.values()))

                    def _row_eval(rec: dict, _code=code, _needed=needed, _funcs=funcs_row):
                        env = {t: _to_float(rec.get(c)) for t, c in _needed.items()}
                        try:
                            out = eval(_code, {"__builtins__": {}}, {**_funcs, **env})  # noqa: S307
                            return None if out is None else float(out)
                        except Exception:
                            return None

                    if struct_cols:
                        out_expr = pl.struct(struct_cols).map_elements(_row_eval, return_dtype=pl.Float64)
                    else:
                        out_expr = pl.lit(_row_eval({}), dtype=pl.Float64)
                    wide = wide.with_columns(out_expr.alias(alias))
                else:
                    env = {
                        token: pl.col(colname).cast(pl.Float64, strict=False)
                        for token, colname in token_map.items()
                    }
                    out = _eval_vehicle_expr(expr_norm, env, group_keys)
                    out_expr = out if isinstance(out, pl.Expr) else pl.lit(out, dtype=pl.Float64)
                    wide = wide.with_columns(out_expr.cast(pl.Float64, strict=False).alias(alias))
            except Exception as e:
                errors.append(f"ADDP '{alias}': 수식 오류 — {e}")
                wide = wide.with_columns(pl.lit(None, dtype=pl.Float64).alias(alias))
                pending.remove(row)
                progressed = True
                continue
            pending.remove(row)
            progressed = True
        if not progressed:
            break
    for row in pending:
        missing = [r for r in _normalize_form(row.get("addp_form") or "")[2] if r not in wide.columns]
        errors.append(f"ADDP '{row['alias']}': 참조 컬럼 미해결 — {missing}")
        wide = wide.with_columns(pl.lit(None, dtype=pl.Float64).alias(str(row["alias"]).strip()))
    return wide, errors
