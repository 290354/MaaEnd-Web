#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"

PY=${PYTHON:-python3}
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "[错误] 未找到 python3"
  exit 1
fi

if [ ! -d .venv ]; then
  echo "首次运行，正在创建虚拟环境..."
  "$PY" -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip -q
  ./.venv/bin/python -m pip install -q -r requirements.txt
fi

exec ./.venv/bin/python main.py "$@"
