#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="${PY:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
  printf '未找到 Python 3，请先安装 Python 3.10+\n' >&2
  exit 1
fi
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "需要 Python 3.10+")'
exec "$PY" "$ROOT/scripts/deploy.py" "$@"
