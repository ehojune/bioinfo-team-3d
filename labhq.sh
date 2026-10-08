#!/bin/sh
set -eu

LABHQ_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
LABHQ_PYTHON="$LABHQ_ROOT/.venv/bin/python"
if [ ! -x "$LABHQ_PYTHON" ]; then
  echo "labhq: .venv가 없습니다. 먼저 Python 가상환경과 labhq를 설치하세요." >&2
  exit 1
fi
PYTHONUTF8=1 LABHQ_LAUNCHER=./labhq.sh exec "$LABHQ_PYTHON" -m labhq.cli "$@"
