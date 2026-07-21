"""Compare Vertex AI Gemini models — quality, tokens, latency."""
import json, os, sys, time
import google.auth
import google.auth.transport.requests
import requests

ADC_PATH = os.path.expanduser("~/.config/gcloud/application_default_credentials.json")
adc = json.load(open(ADC_PATH))
project = adc.get("quota_project_id") or adc.get("project_id")
creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
creds.refresh(google.auth.transport.requests.Request())
print(f"[OK] project={project}\n")

REGION = "us-central1"
MODELS = [
    "gemini-2.5-flash",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.1-pro-preview",
    "gemini-2.5-pro",
    "gemini-3-flash-preview",
]

# 가격 (per 1M tokens, USD) — preview 는 추정, 변경 가능.
PRICING = {
    "gemini-2.5-flash":            (0.10, 0.40),
    "gemini-3.1-flash-lite-preview": (0.05, 0.20),
    "gemini-3.1-pro-preview":      (1.50, 6.00),
    "gemini-2.5-pro":              (1.25, 5.00),
    "gemini-3-flash-preview":      (0.10, 0.40),
}

# agent 가 자주 처리할 task 시뮬 — 한국어 + 추론 + 구조화 응답
PROMPT = """당신은 반도체 fab 데이터를 분석하는 에이전트야. 사용자가 다음 자연어를 던졌어:

"PROD-A의 GATE 모듈에 어제 인폼된 lot 들 list 해줘"

이 요청을 처리하려면 어떤 백엔드 API 를 호출해야 하는지 JSON 으로 답해. 형식:
{
  "tool": "함수 이름",
  "args": {...},
  "follow_up": "사용자에게 더 물어봐야 할 것 (없으면 null)"
}

가능한 tool 후보: inform_list, inform_by_lot, inform_audit_log, inform_lot_matrix.
JSON 만 답해, 다른 설명 X."""

def call(model):
    url = f"https://{REGION}-aiplatform.googleapis.com/v1/projects/{project}/locations/{REGION}/publishers/google/models/{model}:generateContent"
    headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}
    payload = {
        "contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
        "generationConfig": {"maxOutputTokens": 500, "temperature": 0.1},
    }
    t0 = time.monotonic()
    r = requests.post(url, headers=headers, json=payload, timeout=120)
    dt = time.monotonic() - t0
    if r.status_code != 200:
        return {"model": model, "error": f"HTTP {r.status_code}: {r.text[:200]}", "dt_s": dt}
    d = r.json()
    text = "".join(p.get("text","") for c in d.get("candidates",[]) for p in c.get("content",{}).get("parts",[]))
    u = d.get("usageMetadata", {})
    in_t = u.get("promptTokenCount", 0)
    out_t = u.get("candidatesTokenCount", 0)
    think_t = u.get("thoughtsTokenCount", 0)
    total_t = u.get("totalTokenCount", 0)
    p_in, p_out = PRICING.get(model, (0,0))
    cost_usd = (in_t * p_in + (out_t + think_t) * p_out) / 1_000_000
    return {
        "model": model, "dt_s": round(dt, 2),
        "in_tok": in_t, "out_tok": out_t, "think_tok": think_t, "total_tok": total_t,
        "cost_usd_per_call": cost_usd, "cost_per_1k_calls_usd": cost_usd * 1000,
        "response": text.strip()[:400],
    }

results = []
for m in MODELS:
    print(f"[..] {m}")
    r = call(m)
    results.append(r)
    if "error" in r:
        print(f"   ❌ {r['error']}\n")
    else:
        print(f"   ✓ {r['dt_s']}s · in={r['in_tok']} out={r['out_tok']} think={r['think_tok']} · ${r['cost_usd_per_call']:.6f}/call · ${r['cost_per_1k_calls_usd']:.4f}/1k calls")
        print(f"   응답: {r['response'][:200]}\n")

# 요약 표
print("\n" + "=" * 100)
print(f"{'model':<35} {'latency':>10} {'in/out/think':>20} {'$/1k calls':>15} {'품질평가':>15}")
print("-" * 100)
for r in results:
    if "error" in r:
        print(f"{r['model']:<35} {'ERROR':>10}  {r['error'][:60]}")
        continue
    tok = f"{r['in_tok']}/{r['out_tok']}/{r['think_tok']}"
    # JSON 응답 파싱 가능한지 = 품질
    quality = "?"
    try:
        # JSON 만 또는 ```json ... ``` 형태 둘 다 처리
        txt = r['response'].strip()
        if txt.startswith("```"):
            txt = txt.split("```")[1].lstrip("json").strip()
        json.loads(txt)
        quality = "✓ JSON OK"
    except Exception:
        quality = "✗ JSON FAIL"
    print(f"{r['model']:<35} {r['dt_s']:>9}s {tok:>20} ${r['cost_per_1k_calls_usd']:>13.4f} {quality:>15}")

print("=" * 100)
print("\n[참고] 가격은 추정 (preview 모델은 GA 전 변동 가능).")
print("[참고] 300불 ≒ ₩453,000. agent 한 달 1만 호출 = 위 $/1k × 10.")
