#!/usr/bin/env bash
# WolfSec LiveMap launcher for macOS / Linux.
# Usage: ./run_mac_linux.sh            (PORT=9000 ./run_mac_linux.sh to change port)
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8765}"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.9+ was not found."
  if [ "$(uname)" = "Darwin" ]; then
    echo "  Install it from https://www.python.org/downloads/  (or: brew install python)"
  else
    echo "  Debian/Ubuntu: sudo apt install python3 python3-venv"
    echo "  Fedora:        sudo dnf install python3"
    echo "  Arch:          sudo pacman -S python"
  fi
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "[livemap] creating virtual environment…"
  "$PY" -m venv .venv || { echo "venv failed. On Debian/Ubuntu run: sudo apt install python3-venv"; exit 1; }
fi
echo "[livemap] installing/updating dependencies…"
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt

export LIVEMAP_PORT="$PORT" PYTHONUNBUFFERED=1
echo "[livemap] starting on http://localhost:$PORT  (Ctrl+C to stop)"
( sleep 3; (command -v open >/dev/null && open "http://localhost:$PORT") || (command -v xdg-open >/dev/null && xdg-open "http://localhost:$PORT") || true ) >/dev/null 2>&1 &
LIVEMAP_OPEN_BROWSER=0 exec .venv/bin/python -m livemap
