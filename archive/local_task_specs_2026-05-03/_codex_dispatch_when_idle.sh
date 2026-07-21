#!/bin/bash
# Wait for codex-main to go idle, then send prompt for the given spec.
# Use: bash _codex_dispatch_when_idle.sh .codex_task_13_inform_directness_spec.txt
set -u
SESSION="${SESSION:-codex-main}"
SPEC="${1:-.codex_task_13_inform_directness_spec.txt}"
PROMPT="${SPEC} 파일을 먼저 read 도구로 전체 내용 읽고 그 안의 모든 요구사항을 단계별로 빠짐없이 구현해. 시간 충분히 들여도 OK, 작업은 완벽하게 해. git add/commit/push 절대 X. admin_settings.json 절대 수정 X, 내용 출력 X. 작업 마지막에 DISCORD_SUMMARY: 헤더로 한국어 3~5줄 요약 출력."
LOG="/tmp/dispatch_${SPEC//[^A-Za-z0-9_]/_}.log"

echo "[$(date '+%H:%M:%S')] watcher start session=${SESSION} spec=${SPEC}" >> "$LOG"

# Poll until codex-main looks idle (no "Working" / "esc to interrupt" in tail).
while true; do
  TAIL="$(tmux capture-pane -t "$SESSION" -p 2>/dev/null | tail -8)"
  if [ -z "$TAIL" ]; then
    echo "[$(date '+%H:%M:%S')] session vanished, abort" >> "$LOG"
    exit 1
  fi
  if echo "$TAIL" | grep -qE 'Working|esc to interrupt|tokens used|tokens \(esc'; then
    sleep 30
    continue
  fi
  # Extra grace: re-check after 15s to confirm steady idle (not a render gap).
  sleep 15
  TAIL2="$(tmux capture-pane -t "$SESSION" -p 2>/dev/null | tail -8)"
  if echo "$TAIL2" | grep -qE 'Working|esc to interrupt|tokens used|tokens \(esc'; then
    continue
  fi
  # Final 60s grace — gives the orchestrator (claude) time to overwrite spec
  # if the user sends additional requirements after the previous task finishes.
  echo "[$(date '+%H:%M:%S')] idle confirmed, 60s grace before sending" >> "$LOG"
  sleep 60
  TAIL3="$(tmux capture-pane -t "$SESSION" -p 2>/dev/null | tail -8)"
  if echo "$TAIL3" | grep -qE 'Working|esc to interrupt|tokens used|tokens \(esc'; then
    continue
  fi
  break
done

echo "[$(date '+%H:%M:%S')] idle detected, sending prompt" >> "$LOG"
# Type the prompt then Enter.
tmux send-keys -t "$SESSION" -- "$PROMPT"
sleep 1
tmux send-keys -t "$SESSION" Enter
echo "[$(date '+%H:%M:%S')] prompt sent" >> "$LOG"
