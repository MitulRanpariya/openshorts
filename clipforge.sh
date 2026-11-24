#!/usr/bin/env bash
# ClipForge launcher (self-hosted, no Docker needed).
#
#   ./clipforge.sh setup          one-time: Python 3.11 venv + CPU ML stack + dashboard deps
#   ./clipforge.sh start          backend + dashboard (free ports picked automatically)
#   ./clipforge.sh stop           stops only what this script started
#   ./clipforge.sh status         ports, PIDs, YouTube connection
#   ./clipforge.sh logs           follow the backend log
#   ./clipforge.sh test           backend tests + dashboard lint/build
#   ./clipforge.sh youtube-auth   connect your YouTube channel (one time)
#
# Ports: BACKEND_PORT (default 8000) and FRONTEND_PORT (default 5175). A busy
# port is never taken over: the next free one is used instead.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="$ROOT/.clipforge/run"
VENV="$ROOT/.venv"
PY="$VENV/bin/python"
mkdir -p "$RUN"
chmod 700 "$ROOT/.clipforge" 2>/dev/null || true
export PATH="$HOME/.deno/bin:$PATH"

port_busy() { ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$1\$"; }
free_port() {
  local p="$1"
  while port_busy "$p"; do p=$((p + 1)); done
  echo "$p"
}
alive() { [[ -f "$RUN/$1.pid" ]] && kill -0 "$(cat "$RUN/$1.pid")" 2>/dev/null; }

cmd_setup() {
  command -v uv >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/"; exit 1; }
  command -v ffmpeg >/dev/null || { echo "Install ffmpeg (with libass): sudo apt install ffmpeg"; exit 1; }
  command -v node >/dev/null || { echo "Install Node.js 20+"; exit 1; }
  [[ -x "$PY" ]] || uv venv --python 3.11 "$VENV"
  # CPU builds of torch, as the Dockerfile does (PyPI's default pulls GBs of CUDA).
  uv pip install --python "$PY" --index-url https://download.pytorch.org/whl/cpu \
    torch==2.11.0 torchvision==0.26.0
  uv pip install --python "$PY" --no-deps -r "$ROOT/requirements-cpu.lock"
  uv pip install --python "$PY" --no-deps -r "$ROOT/requirements-billing.txt" || true
  uv pip install --python "$PY" pytest
  command -v deno >/dev/null || curl -fsSL https://deno.land/install.sh | \
    DENO_INSTALL="$HOME/.deno" sh -s -- -y --no-modify-path
  (cd "$ROOT/dashboard" && npm ci)
  [[ -f "$ROOT/clipforge.config.yaml" ]] || {
    cp "$ROOT/clipforge.config.example.yaml" "$ROOT/clipforge.config.yaml"
    chmod 600 "$ROOT/clipforge.config.yaml"
    echo "Created clipforge.config.yaml: add your Gemini key (llm.gemini.api_key)."
  }
  echo "Setup done. Next: ./clipforge.sh start"
}

cmd_start() {
  [[ -x "$PY" ]] || { echo "Run ./clipforge.sh setup first."; exit 1; }
  if alive backend; then echo "Already running (./clipforge.sh status)."; exit 0; fi
  local bp fp
  bp=$(free_port "${BACKEND_PORT:-8000}")
  fp=$(free_port "${FRONTEND_PORT:-5175}")
  cd "$ROOT"
  nohup "$VENV/bin/uvicorn" app:app --host 127.0.0.1 --port "$bp" >"$RUN/backend.log" 2>&1 &
  echo $! >"$RUN/backend.pid"
  echo "$bp" >"$RUN/backend.port"
  for _ in $(seq 1 60); do
    curl -sf "http://127.0.0.1:$bp/api/config" >/dev/null && break
    alive backend || { echo "Backend failed to start:"; tail -20 "$RUN/backend.log"; exit 1; }
    sleep 1
  done
  (cd "$ROOT/dashboard" && VITE_PROXY_TARGET="http://127.0.0.1:$bp" \
    nohup npx vite --host 127.0.0.1 --port "$fp" --strictPort >"$RUN/frontend.log" 2>&1 & echo $! >"$RUN/frontend.pid")
  echo "$fp" >"$RUN/frontend.port"
  sleep 3
  echo "Backend : http://127.0.0.1:$bp   (API docs: /docs)"
  echo "Open    : http://127.0.0.1:$fp/#app"
}

cmd_stop() {
  for svc in frontend backend; do
    if alive "$svc"; then
      local pid; pid=$(cat "$RUN/$svc.pid")
      pkill -TERM -P "$pid" 2>/dev/null || true
      kill -TERM "$pid" 2>/dev/null || true
      echo "stopped $svc (pid $pid)"
    fi
    rm -f "$RUN/$svc.pid"
  done
}

cmd_status() {
  for svc in backend frontend; do
    if alive "$svc"; then echo "$svc: running on port $(cat "$RUN/$svc.port") (pid $(cat "$RUN/$svc.pid"))"
    else echo "$svc: stopped"; fi
  done
  [[ -x "$PY" ]] && (cd "$ROOT" && "$PY" youtube_publisher.py status 2>/dev/null) || true
}

case "${1:-}" in
  setup) cmd_setup ;;
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_stop; sleep 1; cmd_start ;;
  status) cmd_status ;;
  logs) tail -f "$RUN/backend.log" ;;
  test) cd "$ROOT" && "$PY" -m pytest tests/ -q && (cd dashboard && npm run lint && npm run build) ;;
  youtube-auth) cd "$ROOT" && "$PY" youtube_publisher.py auth ;;
  *) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
