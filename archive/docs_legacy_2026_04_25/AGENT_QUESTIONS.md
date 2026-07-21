# AGENT_QUESTIONS — 에이전트 → 사용자 질문 라우팅

에이전트가 작업 중 사용자 의사결정이 필요할 때 OmniHarness 의 Questions 탭으로 라우팅하기 위한 규약.

## 트리거 조건 (예시)

1. **스키마 모호** — 요구사항이 두 가지 해석을 허용할 때 (예: 우선순위 체인을 env 우선 vs settings 우선).
2. **기존 호환성 판단** — legacy 경로 폐기 여부, 마이그레이션 창, 백워드 호환 유지 범위 결정이 필요할 때.
3. **외부 종속성** — 사내 IdP, S3 계정, 네트워크 정책 등 에이전트가 단독으로 결정할 수 없는 외부 리소스가 필요할 때.

## POST 포맷

OmniHarness backend 스키마 (`POST /api/questions`): `{agent, raw, context?}`.

```bash
# body
cat > /tmp/q.json <<'EOF'
{
  "agent": "dev-admin",
  "raw": "DB 루트 우선순위를 env 먼저로 유지할까요, admin_settings 를 위로 둘까요?",
  "context": "core/roots.py chain 결정. 샘플 2~3줄로 충분."
}
EOF

curl -s -X POST http://localhost:8082/api/questions \
  -H 'Content-Type: application/json' \
  --data-binary @/tmp/q.json
```

응답은 `{id, agent, raw, translated, answer, status: "pending_translation", created, ...}` 형태. `id` 를 최종 보고서에 기록한다.

## 응답 대기 모델

**비동기 — 기본값으로 진행 + 최종 보고서에 질문 id + 결정 사유 기록.** 에이전트는 질문을 POST 한 뒤 블로킹하지 않고 합리적 기본값으로 작업을 이어간다. 사용자 답변은 `POST /api/questions/{qid}/answer` 로 도착하며, 다음 세션의 orchestrator 가 답변을 보고 재작업/확정 여부를 결정한다. 단일 세션 내에서 답변을 즉시 반영하려면 polling 도 가능하지만 기본 흐름은 비동기.

## 품질 가이드

- **한국어** 로 작성 (사용자 모국어).
- **짧고 구체적** — 한 질문당 1문장 또는 2문장. 배경은 `context` 에 분리.
- **선택지 2~3개 포함** — "A 인지 B 인지, 아니면 C 인 다른 옵션?" 형태로 사용자 결정 비용을 최소화.

---

샘플 질문 id: `e017619f`
