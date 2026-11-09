#!/usr/bin/env bash
# Cross-platform (POSIX) wrapper around run.py.
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  if [[ -x ".venv/bin/python" ]]; then PY=".venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then PY="python3"
  else PY="python"; fi
fi
exec "$PY" run.py "$@"
