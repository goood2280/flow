#!/bin/bash
# Sequential dispatcher for spark codex with session reset between tasks.
# Avoids context_length_exceeded by killing spark + fresh codex per task.
set -u
SESSION="codex-spark"
CODEX_BIN="/home/goood/.nvm/versions/node/v22.22.2/bin/codex"
PROJ="/mnt/d/TEST_Making_Video/semi_all/flow"
LOG="/tmp/spark_chain.log"

SPECS=(
  ".codex_task_17_agent_scenarios_e2e_spec.txt"
  ".codex_task_18_agent_rich_chat_ux_spec.txt"
  ".codex_task_19_inform_wizard_mail_polish_spec.txt"
  ".codex_task_20_agent_gemini_integration_spec.txt"
)

echo "[$(date '+%H:%M:%S')] chain start, ${#SPECS[@]} tasks queued" >> "$LOG"

wait_idle() {
  # Poll until codex-spark looks idle (Working / esc to interrupt absent for 75s).
  local stable=0
  while [ "$stable" -lt 1 ]; do
    local tail
    tail="$(tmux capture-pane -t "$SESSION" -p 2>/dev/null | tail -8)"
    if [ -z "$tail" ]; then
      echo "[$(date '+%H:%M:%S')] spark session vanished, abort" >> "$LOG"
      return 1
    fi
    if echo "$tail" | grep -qE 'Working|esc to interrupt|tokens used|tokens \(esc'; then
      sleep 30
      continue
    fi
    # 15s grace
    sleep 15
    tail="$(tmux capture-pane -t "$SESSION" -p 2>/dev/null | tail -8)"
    if echo "$tail" | grep -qE 'Working|esc to interrupt'; then
      continue
    fi
    # 60s final grace (let user override spec if needed)
    sleep 60
    tail="$(tmux capture-pane -t "$SESSION" -p 2>/dev/null | tail -8)"
    if echo "$tail" | grep -qE 'Working|esc to interrupt'; then
      continue
    fi
    stable=1
  done
  return 0
}

restart_spark() {
  echo "[$(date '+%H:%M:%S')] restarting spark for context reset" >> "$LOG"
  tmux kill-session -t "$SESSION" 2>/dev/null
  sleep 2
  tmux new-session -d -s "$SESSION" -x 200 -y 50
  sleep 1
  tmux send-keys -t "$SESSION" "cd $PROJ" Enter
  sleep 1
  tmux send-keys -t "$SESSION" "$CODEX_BIN -c model=\"gpt-5.3-codex-spark\"" Enter
  sleep 10
  echo "[$(date '+%H:%M:%S')] spark restarted" >> "$LOG"
}

dispatch_task() {
  local spec="$1"
  local prompt="$spec 파일을 read 도구로 전체 내용 읽고 그 안의 모든 요구사항을 단계별로 빠짐없이 구현해. ★ spec 끝의 [CHAIN] 섹션은 무시 — 이 task 만 처리하고 종료. 이전 task 들의 디스크 변경분 보존된 상태에서 시작. git add/commit/push 절대 X. admin_settings.json 절대 수정 X, 내용 출력 X. 작업 마지막에 DISCORD_SUMMARY: 헤더로 한국어 3~5줄 요약 출력."
  echo "[$(date '+%H:%M:%S')] dispatching: $spec" >> "$LOG"
  tmux send-keys -t "$SESSION" -- "$prompt"
  sleep 1
  tmux send-keys -t "$SESSION" Enter
  echo "[$(date '+%H:%M:%S')] sent, waiting for working state..." >> "$LOG"
  sleep 10
}

# Main loop — wait current task (#16) idle then iterate
for spec in "${SPECS[@]}"; do
  echo "[$(date '+%H:%M:%S')] === waiting for previous task to finish ===" >> "$LOG"
  wait_idle || exit 1
  restart_spark
  dispatch_task "$spec"
done

# Wait for last task to also finish
wait_idle
echo "[$(date '+%H:%M:%S')] all tasks complete" >> "$LOG"
