/* hooks/usePolling.js — "끝날 때까지 상태를 물어보는" 폴링을 한 곳으로.

   작업 진행 폴링(캐시 수동 스캔, ET 다운로드 대기열 …)은 화면마다 같은 코드를
   다시 썼다: setInterval + 언마운트 정리 + 연속 실패 카운트 + 최대 틱 제한.
   같은 걸 여러 번 쓰다 보니 실제로 사고가 났다 — 언마운트 후에도 도는 타이머,
   서버가 계속 500 을 주는데 영원히 도는 로딩창.

   계약:
   - `stop()` 을 부르거나 컴포넌트가 사라지면 타이머는 반드시 정리된다.
   - 연속 실패가 `maxErrors` 에 닿으면 스스로 멈추고 `onError(err, "errors")` 를 부른다.
     **무한 로딩을 만들지 않는다.**
   - `maxTicks` 를 넘기면 멈추고 `onError(null, "timeout")`.
   - 첫 조회는 즉시 1회 실행한다(간격만큼 기다리지 않는다).

   사용:
     const poll = usePolling();
     poll.start(() => sf(url), {
       intervalMs: 1200,
       onData: (d) => { if (d.state === "done") poll.stop(); },
       onError: (err, reason) => toast.error(...),
     });
*/
import { useCallback, useEffect, useRef } from "react";

export default function usePolling() {
  const timerRef = useRef(null);
  const aliveRef = useRef(true);

  const stop = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      stop();
    };
  }, [stop]);

  const start = useCallback((fetcher, opts = {}) => {
    const {
      intervalMs = 2000,
      maxErrors = 5,
      maxTicks = 0,          // 0 = 무제한
      onData,
      onError,
    } = opts;
    stop();
    let errors = 0;
    let ticks = 0;
    const tick = () => {
      if (!aliveRef.current) { stop(); return; }
      ticks += 1;
      if (maxTicks > 0 && ticks > maxTicks) {
        stop();
        if (onError) onError(null, "timeout");
        return;
      }
      Promise.resolve()
        .then(fetcher)
        .then((data) => {
          if (!aliveRef.current) { stop(); return; }
          errors = 0;
          if (onData) onData(data);
        })
        .catch((err) => {
          if (!aliveRef.current) { stop(); return; }
          errors += 1;
          if (errors >= maxErrors) {
            stop();
            if (onError) onError(err, "errors");
          }
        });
    };
    timerRef.current = setInterval(tick, intervalMs);
    tick();                       // 첫 조회는 즉시
  }, [stop]);

  const isRunning = useCallback(() => timerRef.current !== null, []);

  return { start, stop, isRunning };
}
