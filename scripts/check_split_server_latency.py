"""Read-only SplitTable latency check on the actual deployed API (stdlib only).

Set FLOW_BENCH_SESSION_TOKEN to a valid session token without putting it in argv.
This checks complete nonempty table responses, never counts 'preparing' as fast,
and does not clear caches or change worker/operator settings. Browser paint must
be checked separately with ?split_perf=1 on the deployed SplitTable page.
"""
import argparse
import json
import math
import os
import time
import urllib.parse
import urllib.request


_TIMING_FIELDS = (
    "total_ms", "compute_ms", "wait_ms", "lane_wait_ms", "cold_lane_wait_ms",
    "singleflight_wait_ms", "prelude_ms", "fastpath_ms", "scan_ms", "root_scan_ms",
    "schema_ms", "fabscope_ms", "select_ms", "collect_ms", "emptyfallback_ms",
    "header_ms", "overlay_ms", "matrix_ms", "mismatch_ms", "progress_ms",
    "payload_ms", "cacheput_ms", "finish_ms", "unaccounted_ms",
)


def response_sample(payload, *, request_number, elapsed_ms, body_bytes):
    rows = payload.get("rows") or payload.get("rows_compact") or []
    profile = payload.get("runtime_profile") or {}
    preparing = bool((payload.get("background_cache") or {}).get("queued"))
    # Only timings/cache flags are printed; never copy usernames or raw metadata.
    timings = {key: value for key in _TIMING_FIELDS
               if isinstance((value := profile.get(key)), (int, float)) and math.isfinite(value)}
    return {"request": request_number, "ms": elapsed_ms, "rows": len(rows),
            "ready": bool(rows) and not preparing and not profile.get("cache_incomplete", False),
            "bytes": body_bytes, "view_cache": payload.get("view_cache"),
            "timings_ms": timings,
            "cache_flags": {key: bool(profile.get(key)) for key in (
                "payload_cache_hit", "root_cache_hit", "knob_sidecar", "fab_index_queued", "cache_incomplete")}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Flow origin, e.g. https://flow.internal")
    parser.add_argument("--product", required=True)
    parser.add_argument("--root-lot", required=True)
    parser.add_argument("--prefix", default="KNOB")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--target-ms", type=float, default=500)
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if not 1 <= args.count <= 100 or args.timeout <= 0 or args.target_ms <= 0:
        parser.error("count must be 1..100, timeout and target-ms must be positive")
    token = os.environ.get("FLOW_BENCH_SESSION_TOKEN", "")
    if not token:
        parser.error("Set FLOW_BENCH_SESSION_TOKEN to the current login session token")
    query = urllib.parse.urlencode({
        "product": args.product, "root_lot_id": args.root_lot,
        "prefix": args.prefix, "history_mode": "all", "view_mode": "all",
    })
    url = args.url.rstrip("/") + "/api/splittable/view?" + query
    samples = []
    for index in range(args.count):
        request = urllib.request.Request(url, headers={"X-Session-Token": token})
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                body = response.read()
            payload = json.loads(body)
            elapsed = round((time.perf_counter() - started) * 1000, 2)
            sample = response_sample(payload, request_number=index + 1,
                                     elapsed_ms=elapsed, body_bytes=len(body))
        except Exception as exc:
            # Do not echo server response bodies or credentials.
            sample = {"request": index + 1, "ready": False, "error": type(exc).__name__}
        samples.append(sample)
        print(json.dumps(sample, ensure_ascii=False))
        if index + 1 < args.count:
            time.sleep(0.25)
    ready_ms = sorted(s["ms"] for s in samples if s["ready"])
    p95 = ready_ms[max(0, math.ceil(len(ready_ms) * 0.95) - 1)] if ready_ms else None
    passed = len(ready_ms) == args.count and p95 <= args.target_ms
    print(json.dumps({"requests": args.count, "ready_responses": len(ready_ms),
                      "p95_ready_ms": p95, "target_ms": args.target_ms,
                      "all_ready_and_p95_within_target": passed,
                      "scope": "HTTP + transfer + JSON parse, excludes browser paint"}))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
