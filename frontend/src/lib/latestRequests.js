// One in-flight request per panel. Reuse identical requests and discard responses
// superseded by a product/filter change, even if the server cannot cancel work.
export function createLatestRequests(fetcher) {
  const pending = new Map();
  return {
    load(key, url, { success, error, done } = {}) {
      const previous = pending.get(key);
      if (previous?.url === url) return previous.promise;
      previous?.controller.abort();
      const request = { url, controller: new AbortController() };
      pending.set(key, request);
      request.promise = Promise.resolve()
        .then(() => fetcher(url, { signal: request.controller.signal }))
        .then(value => { if (pending.get(key) === request) success?.(value); })
        .catch(err => {
          if (pending.get(key) === request && err?.name !== "AbortError") error?.(err);
        })
        .finally(() => {
          if (pending.get(key) === request) {
            pending.delete(key);
            done?.();
          }
        });
      return request.promise;
    },
    cancel(keys = [...pending.keys()]) {
      for (const key of keys) {
        pending.get(key)?.controller.abort();
        pending.delete(key);
      }
    },
  };
}
