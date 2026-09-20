export function createPollingLoop({
  setTimer = setTimeout,
  clearTimer = clearTimeout,
} = {}) {
  let timer = null;
  let generation = 0;
  let running = false;

  const stop = () => {
    generation += 1;
    running = false;
    if (timer !== null) {
      clearTimer(timer);
      timer = null;
    }
  };

  const start = (fetcher, opts = {}) => {
    const {
      intervalMs = 2000,
      maxErrors = 5,
      maxTicks = 0,
      onData,
      onError,
    } = opts;

    stop();
    const runGeneration = generation;
    let errors = 0;
    let ticks = 0;
    running = true;

    const isCurrent = () => running && generation === runGeneration;

    const scheduleNext = () => {
      if (!isCurrent()) return;
      timer = setTimer(() => {
        timer = null;
        tick();
      }, intervalMs);
    };

    const tick = async () => {
      if (!isCurrent()) return;
      ticks += 1;
      if (maxTicks > 0 && ticks > maxTicks) {
        stop();
        if (onError) onError(null, "timeout");
        return;
      }

      try {
        const data = await Promise.resolve().then(() => isCurrent() ? fetcher() : undefined);
        if (!isCurrent()) return;
        errors = 0;
        if (onData) onData(data);
      } catch (err) {
        if (!isCurrent()) return;
        errors += 1;
        if (errors >= maxErrors) {
          stop();
          if (onError) onError(err, "errors");
          return;
        }
      }

      scheduleNext();
    };

    tick();
  };

  return {
    start,
    stop,
    isRunning: () => running,
  };
}
