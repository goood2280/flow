#!/bin/bash
# Codex dispatcher — argv[1] is spec file relative to flow root.
# Use: bash _codex_dispatch.sh .codex_task_2_spec.txt
export PATH=/home/goood/.nvm/versions/node/v22.22.2/bin:$PATH
cd /mnt/d/TEST_Making_Video/semi_all/flow
SPEC_PATH="${1:-.codex_task_2_spec.txt}"
PROMPT="${SPEC_PATH} 파일을 먼저 read 도구로 전체 내용 읽고 그 안의 모든 요구사항(A~L 섹션 또는 spec 안의 모든 항목)을 단계별로 빠짐없이 구현해. 시간 충분히 들여도 OK, 작업은 완벽하게 해. git add/commit/push 절대 X. admin_settings.json 절대 수정 X, 내용 출력 X. 작업 마지막에 DISCORD_SUMMARY: 헤더로 한국어 3~5줄 요약 출력."
exec codex -c model_reasoning_effort=xhigh "$PROMPT"
