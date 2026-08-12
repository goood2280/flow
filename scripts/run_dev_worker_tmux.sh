#!/usr/bin/env bash
# tmux 상주용 Flow 개발 워커 supervisor.
#
# 사용:
#   tmux new -s flow-dev
#   bash scripts/run_dev_worker_tmux.sh
#
# 또는 한 줄로 백그라운드 세션 생성:
#   tmux new-session -d -s flow-dev 'bash scripts/run_dev_worker_tmux.sh'
#   tmux attach -t flow-dev
#
# uvicorn이 OOM/SIGKILL 또는 오류로 종료되면 worker_watchdog.py가 자동 재기동한다.
# 로그: $FLOW_DATA_ROOT/worker/control/worker_uvicorn.log
set -uo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
APP_ROOT="${FLOW_APP_ROOT:-$(dirname -- "$SCRIPT_DIR")}"
PYTHON_BIN="${FLOW_PYTHON:-python3}"

export FLOW_APP_ROOT="$APP_ROOT"
export FLOW_SERVER_ROLE="worker"
export FLOW_DATA_ROOT="${FLOW_DATA_ROOT:-/config/work/sharedworkspace/flow-data}"
export FLOW_DB_ROOT="${FLOW_DB_ROOT:-/config/work/sharedworkspace/DB}"
export FLOW_WORKER_CONCURRENCY="${FLOW_WORKER_CONCURRENCY:-1}"
export FLOW_WORKER_PORT="${FLOW_WORKER_PORT:-8080}"
export PYTHONUNBUFFERED="1"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "[flow-dev] python executable not found: $PYTHON_BIN" >&2
  exit 127
fi
if [[ ! -f "$APP_ROOT/app.py" ]]; then
  echo "[flow-dev] app.py not found under FLOW_APP_ROOT=$APP_ROOT" >&2
  exit 2
fi

echo "[flow-dev] app_root=$APP_ROOT"
echo "[flow-dev] data_root=$FLOW_DATA_ROOT"
echo "[flow-dev] db_root=$FLOW_DB_ROOT"
echo "[flow-dev] port=$FLOW_WORKER_PORT python=$PYTHON_BIN"
echo "[flow-dev] Ctrl-C stops the supervisor and its uvicorn child."

exec "$PYTHON_BIN" "$APP_ROOT/scripts/worker_watchdog.py" \
  --keep-alive \
  --port "$FLOW_WORKER_PORT" \
  --python "$PYTHON_BIN" \
  --restart-delay "${FLOW_WORKER_RESTART_DELAY_SEC:-5}" \
  --max-restart-delay "${FLOW_WORKER_MAX_RESTART_DELAY_SEC:-120}"
