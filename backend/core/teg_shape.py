"""core/teg_shape.py — shot 그림에서 die(칩) 사각형만 추려낸다.

TEG 위치 조회의 shot 표시 방식이 "그림"인 vehicle 은 칩 격자(cols/rows) 대신
붙여넣은 그림 한 장으로 shot 안을 표현한다. 그림은 흰 배경 위에 회색·녹색·분홍
등으로 칠해진(또는 선으로만 그린) 사각형들이 놓인 형태다.

그림에서 사각형을 추려내는 것은 화면 안내용이다("die 사각형 N개 인식"). 색은
제품/툴마다 제각각이라 근거로 쓸 수 없으므로 **색을 버리고 배경 여부만 남긴 뒤,
윤곽이 네 변을 모두 채우는 덩어리만** 사각형으로 인정한다. 원·삼각형·글자·화살표는
이 조건에서 자연히 걸러진다.

사각형은 두 단계로 찾는다. ① 연결 덩어리의 bbox 네 변이 채워진 것(떨어져 있는
die). ② 그걸로 못 찾으면 **경계선(ruling) 격자 복원** — die 가 맞닿아 테두리를
공유하면 그림 전체가 한 덩어리가 돼 ①이 아무것도 못 찾는다. 가로·세로로 길게
이어진 줄을 die 경계로 보고 그 사이 칸을 셀로 되살린다.

die 셀은 shot 표시 방식에 따라 두 곳에서 나온다.
  · 그림(image)      — 그림에서 인식한 사각형을 그대로 shot mm 로 환산(image_cells).
                       화면의 그림과 정확히 겹쳐 보이는 격자다.
  · 개발 격자(dev_grid) — `MAIN` 으로 이름 붙은 TEG 좌표(= die 좌하단)에
                       Main_chip_info.csv(µm)의 chip 크기로 그린다(anchor_cells).
                       그림이 없어도 되고, 크기를 모르는 제품은 아무것도 안 그린다.

반환하는 셀은 core/teg_check._chip_cells 와 같은 규약이다 — {x, y, w, h} (mm,
shot 센터 기준, 좌표 = 셀 좌하단). 그래서 _overlaps_chip 을 그대로 쓸 수 있고,
칩 격자 모드와 그림 모드가 같은 판정 코드를 공유한다.

의존: Pillow(디코딩) + numpy. 연결요소 라벨링은 numpy 만으로 직접 한다 —
scipy.ndimage.label 이 더 짧지만 scipy 는 설치가 보장되지 않는 환경이 있고,
die 판정이 조용히 빠지는 것보다 의존을 줄이는 쪽이 낫다. Pillow 가 없으면 예외
대신 reason 을 담은 결과를 돌려준다 — 그림 모드 자체는 계속 동작해야 한다.
"""
from __future__ import annotations

import io
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("flow.teg_shape")

# 판정 파라미터 — 그림은 대부분 캡처/스크린샷이라 여유 있게 잡는다.
BG_TOL = 12             # 배경색과 이만큼 넘게 다르면 도형(선/채움)으로 본다
BORDER_PX = 2           # 배경색을 읽는 가장자리 두께
RING_BG_RATIO = 0.5     # 가장자리 최빈색이 이 비율은 돼야 배경으로 인정
MIN_BG_RATIO = 0.25     # 폴백(전체 최빈색) 기준 — 못 넘으면 평평한 배경이 아니다
MAX_SIDE = 1400         # 이보다 큰 그림은 축소 후 판정 (위치만 필요하므로 충분)
MIN_SIDE_RATIO = 0.015  # 짧은 변이 이미지 짧은 변의 1.5% 미만이면 글자/노이즈
EDGE_COVER = 0.80       # bbox 네 변이 이 비율 이상 덮여야 '사각형 틀'
EDGE_BAND_RATIO = 0.06  # 변 판정에 쓰는 띠 두께(안티에일리어싱 선 흡수용)
MAX_AREA_RATIO = 0.985  # 그림 전체를 감싸는 바깥 테두리는 die 가 아니다
CONTAIN_RATIO = 0.95    # 큰 사각형에 이만큼 들어가면 중복으로 보고 버린다
MAX_RECTS = 400         # 이보다 많이 나오면 노이즈로 보고 자른다
MAX_RUNS = 300_000      # 라벨링 복잡도 상한 — 디더링/사진처럼 잡한 그림은 포기한다
# 맞닿은 die 복원(2단계) — 경계선을 찾아 그 사이 칸을 셀로 되살린다.
RULE_COVER = 0.55       # 가로/세로로 이 비율 이상 이어진 줄 = die 경계선
MAX_GRID_LINES = 80     # 경계선이 이보다 많으면 격자가 아니라 잡음으로 본다
MIN_CELL_RATIO = 0.02   # 격자 셀 최소 변 (그림 짧은 변 대비)
GRID_EDGE_COVER = 0.60  # 격자 칸의 네 변 판정 — 끊긴 선을 감안해 1단계보다 느슨하다


def _err(reason: str) -> dict[str, Any]:
    return {"ok": False, "reason": reason, "rects": [], "count": 0}


def _background(arr, np):
    """배경색 추정 — 못 찾으면 None.

    보통은 흰 배경이지만 어두운 테마로 캡처한 그림도 들어온다. 흰색으로 고정하면
    그런 그림은 전체가 한 덩어리가 돼 아무것도 못 찾는다. 색 자체는 판정에 쓰지
    않으므로(요구사항: 색 제외) 배경이 무슨 색인지만 알아내면 된다.

    1순위는 **가장자리 한 줄의 최빈색** — die 가 촘촘해 배경 면적이 작은 배치도
    에서도 캔버스는 바깥 테두리에 남는다. 가장자리가 균일하지 않으면(테두리까지
    도형이 닿은 그림) 전체 최빈색으로 물러선다.

    JPEG 잡티·안티에일리어싱을 흡수하려고 채널당 32단계로 뭉쳐 세고, 대표색은 그
    버킷의 실제 평균으로 되돌린다.
    """
    q = (arr >> 3).astype(np.int32)
    codes = (q[..., 0] << 10) | (q[..., 1] << 5) | q[..., 2]

    def _mode(sample, floor_ratio):
        counts = np.bincount(sample.ravel(), minlength=1 << 15)
        top = int(counts.argmax())
        return top if counts[top] >= sample.size * floor_ratio else None

    ring = np.concatenate([codes[:BORDER_PX, :].ravel(), codes[-BORDER_PX:, :].ravel(),
                           codes[:, :BORDER_PX].ravel(), codes[:, -BORDER_PX:].ravel()])
    top = _mode(ring, RING_BG_RATIO)
    if top is None:
        top = _mode(codes, MIN_BG_RATIO)
    if top is None:
        return None
    return arr[codes == top].mean(axis=0)


def _to_mask(data: bytes):
    """그림 바이트 → (배경이 아닌 픽셀 마스크, 밝기, 원본 크기). 색은 여기서 버린다.

    밝기(gray)는 경계선 격자 복원에만 쓴다 — 채워진 die 는 마스크가 통째로 True 라
    내부 경계가 안 보이지만, 밝기 차이(gradient)로는 보인다. 색상값 자체가 아니라
    '옆 픽셀과 다른가'만 쓰므로 색을 판정에 넣지 않는다는 규칙은 그대로다.
    """
    from PIL import Image
    import numpy as np

    with Image.open(io.BytesIO(data)) as im:
        src_w, src_h = im.size
        im = im.convert("RGB")
        longest = max(src_w, src_h)
        if longest > MAX_SIDE:
            k = MAX_SIDE / float(longest)
            im = im.resize((max(1, int(src_w * k)), max(1, int(src_h * k))),
                           Image.BILINEAR)
        arr = np.asarray(im, dtype=np.uint8)
    bg = _background(arr, np)
    if bg is None:
        return None, None, src_w, src_h
    # 배경과 다르면 도형 — 회색·녹색·분홍 채움도, 선만 그린 것도 함께 잡힌다.
    ink = np.abs(arr.astype(np.int16) - bg.astype(np.int16)).max(axis=2) > BG_TOL
    gray = arr.astype(np.int16).mean(axis=2)
    return ink, gray, src_w, src_h


def _edge_covered(sub, cover: float = EDGE_COVER) -> bool:
    """bbox 네 변이 모두 채워져 있는가 — 채운 사각형·선만 그린 사각형 둘 다 통과.

    한 픽셀 선만 보면 안티에일리어싱으로 끊긴 곳에서 실패하므로, 변마다 얇은 띠를
    잡아 그 안에 픽셀이 하나라도 있으면 덮인 것으로 센다.
    """
    h, w = sub.shape
    if h < 2 or w < 2:
        return False
    tb = max(1, min(h // 2, int(round(h * EDGE_BAND_RATIO))))
    lr = max(1, min(w // 2, int(round(w * EDGE_BAND_RATIO))))
    return (sub[:tb, :].any(axis=0).mean() >= cover
            and sub[-tb:, :].any(axis=0).mean() >= cover
            and sub[:, :lr].any(axis=1).mean() >= cover
            and sub[:, -lr:].any(axis=1).mean() >= cover)


def _bands(flags, np) -> list[tuple[int, int]]:
    """True 인 구간 [(start, end_exclusive), ...] — 선 두께를 한 줄로 뭉치기 위한 것."""
    f = np.concatenate(([False], np.asarray(flags, dtype=bool), [False]))
    d = np.diff(f.astype(np.int8))
    return list(zip(np.flatnonzero(d == 1).tolist(), np.flatnonzero(d == -1).tolist()))


def _rulings(cover, min_gap: float, np) -> list[float]:
    """경계선 위치 — 커버리지가 높은 구간의 중심. 붙어 있는 구간은 하나로 묶는다.

    선 하나는 양쪽에 밝기 단차를 만들어 구간이 둘로 갈라진다. min_gap(최소 셀 변)
    보다 가까운 구간은 같은 선으로 보고 합쳐 유령 셀이 끼지 않게 한다.
    """
    bands = _bands(cover >= RULE_COVER, np)
    merged: list[list[int]] = []
    for a, b in bands:
        if merged and a - merged[-1][1] < min_gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a + b) / 2.0 for a, b in merged]


def _grid_rects(ink, gray, np) -> list[tuple[float, float, float, float]]:
    """맞닿은 die 복원 — 경계선(ruling) 사이 칸을 사각형으로 되살린다.

    die 가 테두리를 공유하면 그림 전체가 한 덩어리라 연결요소로는 아무것도 못
    찾는다. 그런 배치도는 가로·세로로 길게 이어진 줄이 곧 die 경계이므로, 밝기가
    옆 픽셀과 갈리는 지점(단차)을 모아 그 줄을 찾고 사이 칸을 셀로 만든다. 단차를
    쓰는 이유는 die 가 색으로 채워져 있어도 내부 경계가 보이기 때문이다. 칸이 실제로
    네 변에 둘러싸여 있는지 한 번 더 확인해 L 자·계단형 배치에서 빈 칸을 거른다.
    """
    h, w = ink.shape
    ys, xs = np.nonzero(ink)
    if not len(ys):
        return []
    # 바깥 테두리의 단차가 잘리지 않도록 ink bbox 를 1px 넓혀서 본다.
    y0, y1 = max(0, int(ys.min()) - 1), min(h, int(ys.max()) + 2)
    x0, x1 = max(0, int(xs.min()) - 1), min(w, int(xs.max()) + 2)
    g = gray[y0:y1, x0:x1]
    if g.shape[0] < 4 or g.shape[1] < 4:
        return []
    min_side = max(3.0, min(w, h) * MIN_CELL_RATIO)
    # diff 인덱스 j = 픽셀 j 와 j+1 사이 → 경계 위치는 j + 0.5
    cols = _rulings((np.abs(np.diff(g, axis=1)) > BG_TOL).mean(axis=0), min_side, np)
    rows = _rulings((np.abs(np.diff(g, axis=0)) > BG_TOL).mean(axis=1), min_side, np)
    if not (2 <= len(rows) <= MAX_GRID_LINES and 2 <= len(cols) <= MAX_GRID_LINES):
        return []
    ry = [v + 0.5 for v in rows]
    rx = [v + 0.5 for v in cols]
    out: list[tuple[float, float, float, float]] = []
    for i in range(len(rx) - 1):
        cx0, cx1 = rx[i], rx[i + 1]
        if cx1 - cx0 < min_side:
            continue
        for j in range(len(ry) - 1):
            cy0, cy1 = ry[j], ry[j + 1]
            if cy1 - cy0 < min_side:
                continue
            # 경계선 중심에서 잘리므로 선 두께 절반이 밖으로 나간다 — 1px 넓혀 본다.
            a0 = max(0, x0 + int(round(cx0)) - 1)
            a1 = min(w, x0 + int(round(cx1)) + 1)
            b0 = max(0, y0 + int(round(cy0)) - 1)
            b1 = min(h, y0 + int(round(cy1)) + 1)
            if not _edge_covered(ink[b0:b1, a0:a1], GRID_EDGE_COVER):
                continue
            out.append(((x0 + cx0) / w, (y0 + cy0) / h, (x0 + cx1) / w, (y0 + cy1) / h))
            if len(out) >= MAX_RECTS:
                return out
    return out


def _label(mask, np):
    """8-연결 연결요소 라벨링 — (labels, 라벨수). 라벨 0 = 배경.

    행마다 True 구간(run)을 뽑아 윗행 run 과 겹치면 union-find 로 합친다. 배치도
    그림은 도형이 몇 개뿐이라 run 이 적어 충분히 빠르다. 잡티가 많은 그림(사진,
    디더링)은 run 이 폭증하므로 MAX_RUNS 에서 포기한다 — 그런 그림은 어차피
    사각형 판정이 의미가 없다.
    """
    h, w = mask.shape
    labels = np.zeros((h, w), dtype=np.int32)
    parent = [0]

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    prev: list[tuple[int, int, int]] = []
    total_runs = 0
    pad = np.zeros(w + 2, dtype=np.int8)
    for y in range(h):
        row = mask[y]
        if not row.any():
            prev = []
            continue
        pad[1:w + 1] = row
        diff = np.diff(pad)
        starts = np.flatnonzero(diff == 1)
        ends = np.flatnonzero(diff == -1)
        total_runs += len(starts)
        if total_runs > MAX_RUNS:
            return None, -1
        cur: list[tuple[int, int, int]] = []
        for s, e in zip(starts.tolist(), ends.tolist()):
            lab = 0
            # 8-연결이라 대각선 접촉(한 칸 여유)도 같은 덩어리로 본다.
            for ps, pe, pl in prev:
                if ps <= e and pe >= s:
                    root = find(pl)
                    if lab == 0:
                        lab = root
                    elif root != lab:
                        hi, lo = max(root, lab), min(root, lab)
                        parent[hi] = lo
                        lab = lo
            if lab == 0:
                parent.append(len(parent))
                lab = len(parent) - 1
            labels[y, s:e] = lab
            cur.append((s, e, lab))
        prev = cur

    if len(parent) <= 1:
        return labels, 0
    # union 결과를 대표 라벨로 눌러 담고 1..n 으로 다시 번호를 매긴다.
    roots = np.array([find(i) for i in range(len(parent))], dtype=np.int32)
    uniq = np.unique(roots[1:])
    remap = np.zeros(len(parent), dtype=np.int32)
    remap[uniq] = np.arange(1, len(uniq) + 1, dtype=np.int32)
    return remap[roots][labels], int(len(uniq))


def _bboxes(labels, n: int, np) -> list[tuple[int, int, int, int]]:
    """라벨별 bbox (x0, y0, x1, y1) — x1/y1 은 exclusive. 인덱스 = 라벨-1."""
    ys, xs = np.nonzero(labels)
    lv = labels[ys, xs]
    big = np.iinfo(np.int32).max
    x0 = np.full(n + 1, big, np.int32)
    y0 = np.full(n + 1, big, np.int32)
    x1 = np.full(n + 1, -1, np.int32)
    y1 = np.full(n + 1, -1, np.int32)
    np.minimum.at(x0, lv, xs)
    np.minimum.at(y0, lv, ys)
    np.maximum.at(x1, lv, xs)
    np.maximum.at(y1, lv, ys)
    return [(int(x0[i]), int(y0[i]), int(x1[i]) + 1, int(y1[i]) + 1)
            for i in range(1, n + 1)]


def _drop_contained(rects: list[tuple[float, float, float, float]]) -> list[tuple]:
    """포함 관계 정리 — 같은 die 를 두 번 세지 않고, 여러 die 를 감싼 '틀'은 버린다.

    큰 사각형이 작은 것을 **하나만** 품으면 같은 die 를 테두리+채움으로 두 번 그린
    것으로 보고 큰 쪽을 남긴다. **둘 이상**을 품으면 그건 die 가 아니라 배치 틀이다 —
    틀을 die 로 두면 shot 안 TEG 가 전부 'die 안'이 돼 판정이 무의미해진다.
    """
    n = len(rects)
    def _area(r):
        return max((r[2] - r[0]) * (r[3] - r[1]), 1e-12)
    order = sorted(range(n), key=lambda i: _area(rects[i]), reverse=True)
    rank = {i: k for k, i in enumerate(order)}
    inside: dict[int, list[int]] = {i: [] for i in range(n)}
    for i in range(n):
        bx0, by0, bx1, by1 = rects[i]
        for j in range(n):
            if rank[j] <= rank[i]:      # j 가 i 보다 크면(또는 같으면) 품을 수 없다
                continue
            sx0, sy0, sx1, sy1 = rects[j]
            ix = max(0.0, min(sx1, bx1) - max(sx0, bx0))
            iy = max(0.0, min(sy1, by1) - max(sy0, by0))
            if ix * iy / _area(rects[j]) >= CONTAIN_RATIO:
                inside[i].append(j)
    dropped: set[int] = set()
    for i in order:
        if i in dropped:
            continue
        kids = [j for j in inside[i] if j not in dropped]
        if len(kids) >= 2:
            dropped.add(i)              # 틀
        else:
            dropped.update(kids)        # 같은 die 중복
    return [r for i, r in enumerate(rects) if i not in dropped]


def _detect_bytes(data: bytes) -> dict[str, Any]:
    try:
        import numpy as np
    except ImportError:
        return _err("missing_dependency:numpy")
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return _err("missing_dependency:PIL")
    try:
        ink, gray, src_w, src_h = _to_mask(data)
    except Exception as exc:
        logger.warning("teg shape decode failed: %s", exc, exc_info=True)
        return _err(f"decode_failed:{type(exc).__name__}")

    empty = {"ok": True, "reason": "no_shape", "rects": [], "count": 0,
             "source": "", "img_w": src_w, "img_h": src_h}
    if ink is None:
        # 평평한 배경이 없는 그림(사진 등) — 사각형 판정이 의미가 없다.
        return {**empty, "reason": "no_flat_background"}
    h, w = ink.shape
    if not ink.any():
        return empty
    labels, n = _label(ink, np)
    if n < 0:
        return {**empty, "reason": "too_complex"}
    if n == 0:
        return empty
    min_side = max(3, int(round(min(w, h) * MIN_SIDE_RATIO)))
    found: list[tuple[float, float, float, float]] = []
    for i, (bx0, by0, bx1, by1) in enumerate(_bboxes(labels, n, np), start=1):
        bw, bh = bx1 - bx0, by1 - by0
        if bw < min_side or bh < min_side:
            continue
        if bw * bh >= w * h * MAX_AREA_RATIO:
            continue
        if not _edge_covered(labels[by0:by1, bx0:bx1] == i):
            continue
        found.append((bx0 / w, by0 / h, bx1 / w, by1 / h))

    truncated = len(found) > MAX_RECTS
    kept = _drop_contained(found[:MAX_RECTS] if truncated else found)
    source = "blob" if kept else ""
    # 떨어져 있는 die 를 못 찾았으면(대개 테두리를 공유해 한 덩어리가 된 배치도)
    # 경계선 격자로 되살려 본다. 바깥 테두리 하나만 남은 경우도 여기로 온다.
    if len(kept) <= 1:
        grid = _grid_rects(ink, gray, np)
        if len(grid) >= 2:
            kept, truncated, source = grid, len(grid) >= MAX_RECTS, "grid"
    return {
        "ok": True,
        "reason": "truncated" if truncated else ("no_shape" if not kept else ""),
        "img_w": src_w, "img_h": src_h,
        "source": source,
        "count": len(kept),
        "rects": [{"x0": round(a, 6), "y0": round(b, 6),
                   "x1": round(c, 6), "y1": round(d, 6)} for a, b, c, d in kept],
    }


# 같은 그림을 Mapfile 체크마다 다시 분석하지 않는다. 키 = 경로, 유효성 = (mtime, size).
_CACHE: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 64


def detect(path: Path | str) -> dict[str, Any]:
    """그림 파일에서 die 사각형 추출 (정규화 좌표 0~1, 원점 = 그림 좌상단)."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return _err("not_found")
    key = str(p)
    sig = (st.st_mtime_ns, st.st_size)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None and hit[0] == sig:
            return hit[1]
    try:
        result = _detect_bytes(p.read_bytes())
    except OSError as exc:
        return _err(f"read_failed:{type(exc).__name__}")
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = (sig, result)
    return result


def invalidate(path: Path | str | None = None) -> None:
    """그림이 바뀌었을 때 캐시 무효화 (path 없으면 전체)."""
    with _CACHE_LOCK:
        if path is None:
            _CACHE.clear()
        else:
            _CACHE.pop(str(Path(path)), None)


def anchor_cells(anchors: list[dict]) -> tuple[list[dict], dict]:
    """MAIN 앵커 → die 셀 (셀, 요약) — **개발 격자(dev_grid) 모드**의 die.

    앵커 = [{name, x, y, (w, h)}] — MAIN TEG 좌표(mm)이고 그 좌표가 die 좌하단이다.
    **크기(Main_chip_info.csv)가 있는 앵커만 셀이 된다.** 크기를 모르는 제품에
    아무 크기나 넣어 사각형을 그리면 실제와 다른 네모가 얹혀 오히려 헷갈린다
    (2026-07-27 사용자 확인) — 근거가 없으면 아무것도 그리지 않고 판정을 건너뛴다.

    앵커 이름(MAIN01 등)은 셀에 그대로 실어 보낸다 — MAIN 내부 TEG 가 **자기**
    MAIN die 안에 있는지 판정하려면 셀이 어느 die 인지 알아야 한다
    (core/teg_check.main_die_light).
    """
    anchors = list(anchors or [])
    sized = [a for a in anchors if a.get("w") and a.get("h")]
    cells = [{"name": str(a.get("name") or ""),
              "x": round(float(a["x"]), 6), "y": round(float(a["y"]), 6),
              "w": round(float(a["w"]), 6), "h": round(float(a["h"]), 6)} for a in sized]
    return cells, {"anchors": len(anchors), "sized": len(sized)}


def image_cells(path: Path | str, shot_w_mm: float, shot_h_mm: float) -> list[dict]:
    """그림에서 인식한 사각형 → shot 좌표(mm) 셀 — **그림(image) 모드**의 die.

    그림은 shot 사각형에 꽉 채워 그려지므로(프론트 preserveAspectRatio="none")
    정규화 좌표를 그대로 mm 로 환산한다. 그림 y 는 아래로, shot mm y 는 위로
    증가하므로 y 는 뒤집는다. 그래서 이 셀은 화면의 그림과 정확히 겹쳐 보인다.
    """
    W, H = float(shot_w_mm), float(shot_h_mm)
    cells: list[dict] = []
    for r in detect(path).get("rects") or []:
        x0, x1 = r["x0"] * W - W / 2, r["x1"] * W - W / 2
        y_top, y_bottom = H / 2 - r["y0"] * H, H / 2 - r["y1"] * H
        if x1 > x0 and y_top > y_bottom:
            cells.append({"x": round(x0, 6), "y": round(y_bottom, 6),
                          "w": round(x1 - x0, 6), "h": round(y_top - y_bottom, 6)})
    return cells


def shot_cells(path: Path | str, shot_w_mm: float, shot_h_mm: float,
               anchors: list[dict] | None = None, *, dev_grid: bool = False) -> list[dict]:
    """die 셀 목록 (mm, 좌표=셀 좌하단) — _chip_cells 와 같은 규약."""
    return shot_cells_detail(path, shot_w_mm, shot_h_mm, anchors, dev_grid=dev_grid)["cells"]


def shot_cells_detail(path: Path | str, shot_w_mm: float, shot_h_mm: float,
                      anchors: list[dict] | None = None, *, dev_grid: bool = False) -> dict:
    """die 셀 + 그림 인식 요약 — 화면이 "무엇을 die 로 봤는지" 설명할 수 있게.

    dev_grid=True  → 셀은 MAIN 앵커(위치) + Main_chip_info(크기)에서만 나온다.
    dev_grid=False → 셀은 그림에서 인식한 사각형이다(그림과 겹쳐 보이는 격자).
    어느 쪽이든 인식 결과(image_count/source/reason)는 화면 안내용으로 함께 준다.
    """
    det = detect(path)
    image = image_cells(path, shot_w_mm, shot_h_mm)
    if dev_grid:
        cells, align = anchor_cells(anchors)
    else:
        cells, align = image, {"anchors": len(anchors or []), "sized": 0}
    return {"cells": cells, "image_cells": image, "align": align,
            "image_count": int(det.get("count") or 0),
            "source": det.get("source") or "", "reason": det.get("reason") or ""}
