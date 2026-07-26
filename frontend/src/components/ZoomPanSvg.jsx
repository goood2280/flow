/* ZoomPanSvg.jsx — 마우스 휠(커서 중심 확대/축소) + 드래그(패닝) + 핀치 줌 + 리셋 버튼을
   제공하는 공용 SVG 래퍼. TEG 위치 조회 ShotZoom / TEG Mapfile 체크 ShotView 에서
   중복 구현되던 zoom/pan 로직을 단일 소스로 추출 (v9.5.x).
   children 은 함수형 — 현재 zoom 값을 받아 <g> 내부 콘텐츠를 그린다
   (strokeWidth={0.8/zoom} 처럼 줌 불변 두께를 유지하기 위함). */
import { useCallback, useEffect, useRef, useState } from "react";

const ZOOM_MIN = 1, ZOOM_MAX = 12, ZOOM_STEP = 1.15;

export default function ZoomPanSvg({ size = 380, style = {}, children }) {
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const svgRef = useRef(null);
  const dragRef = useRef(null);           // { startX, startY, panX0, panY0 } | null
  const pinchRef = useRef(null);          // { dist0, zoom0 } | null
  const pointersRef = useRef(new Map());  // pointerId → { x, y }

  const resetView = useCallback(() => { setZoom(1); setPan({ x: 0, y: 0 }); }, []);

  // 마우스 휠 → 줌 (커서 중심)
  const onWheel = useCallback((e) => {
    e.preventDefault();
    const rect = svgRef.current.getBoundingClientRect();
    const mx = e.clientX - rect.left, my = e.clientY - rect.top;
    setZoom(prev => {
      const next = e.deltaY < 0
        ? Math.min(ZOOM_MAX, prev * ZOOM_STEP)
        : Math.max(ZOOM_MIN, prev / ZOOM_STEP);
      const ratio = 1 - next / prev;
      setPan(p => ({ x: p.x + (mx - p.x) * ratio, y: p.y + (my - p.y) * ratio }));
      return next;
    });
  }, []);

  const pinchDist = (pts) => {
    const arr = [...pts.values()];
    if (arr.length < 2) return 0;
    return Math.hypot(arr[0].x - arr[1].x, arr[0].y - arr[1].y);
  };
  const onPointerDown = useCallback((e) => {
    svgRef.current?.setPointerCapture(e.pointerId);
    pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointersRef.current.size === 2) {
      pinchRef.current = { dist0: pinchDist(pointersRef.current), zoom0: zoom };
      dragRef.current = null;
    } else if (pointersRef.current.size === 1) {
      dragRef.current = { startX: e.clientX, startY: e.clientY, panX0: pan.x, panY0: pan.y };
    }
  }, [zoom, pan]);
  const onPointerMove = useCallback((e) => {
    pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (pointersRef.current.size === 2 && pinchRef.current) {
      const d = pinchDist(pointersRef.current);
      if (pinchRef.current.dist0 > 0) {
        setZoom(Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, pinchRef.current.zoom0 * (d / pinchRef.current.dist0))));
      }
    } else if (dragRef.current && pointersRef.current.size === 1) {
      setPan({ x: dragRef.current.panX0 + (e.clientX - dragRef.current.startX),
               y: dragRef.current.panY0 + (e.clientY - dragRef.current.startY) });
    }
  }, []);
  const onPointerUp = useCallback((e) => {
    pointersRef.current.delete(e.pointerId);
    if (pointersRef.current.size < 2) pinchRef.current = null;
    if (pointersRef.current.size === 0) dragRef.current = null;
  }, []);

  // 휠 이벤트는 passive:false 필요 → ref 방식으로 등록
  useEffect(() => {
    const el = svgRef.current;
    if (!el) return;
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [onWheel]);

  const isZoomed = zoom !== 1 || pan.x !== 0 || pan.y !== 0;

  return (
    <div style={{ position: "relative", display: "inline-block" }}>
      <svg ref={svgRef} width={size} height={size} viewBox={`0 0 ${size} ${size}`}
        style={{ background: "var(--bg-card)", border: "1px solid var(--line)", borderRadius: 6,
                 cursor: isZoomed ? "grab" : "zoom-in", touchAction: "none", userSelect: "none",
                 ...style }}
        onPointerDown={onPointerDown} onPointerMove={onPointerMove}
        onPointerUp={onPointerUp} onPointerCancel={onPointerUp}>
        <g transform={`translate(${pan.x},${pan.y}) scale(${zoom})`}>
          {typeof children === "function" ? children(zoom) : children}
        </g>
      </svg>
      {/* 줌 리셋 버튼 — 줌/패닝 상태일 때만 표시 */}
      {isZoomed && (
        <button onClick={resetView} title="보기 초기화"
          style={{ position: "absolute", top: 6, right: 6, width: 28, height: 28,
                   display: "flex", alignItems: "center", justifyContent: "center",
                   background: "var(--bg-card)", border: "1px solid var(--line)", borderRadius: 4,
                   cursor: "pointer", fontSize: 14, color: "var(--muted)", opacity: 0.85 }}>
          ↺
        </button>
      )}
      {/* 줌 배율 표시 */}
      {zoom > 1.05 && (
        <span style={{ position: "absolute", bottom: 6, right: 6, fontSize: 11,
                       color: "var(--muted)", background: "var(--bg-card)", padding: "1px 5px",
                       borderRadius: 3, border: "1px solid var(--line)", opacity: 0.8 }}>
          ×{zoom.toFixed(1)}
        </span>
      )}
    </div>
  );
}
