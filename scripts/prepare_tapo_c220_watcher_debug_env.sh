#!/usr/bin/env bash

set -euo pipefail

# Tapo C220 watcher の VSCode デバッグ用 venv を準備する。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WATCHER_DIR="${REPO_ROOT}/watchers/tapo_c220"
VENV_DIR="${WATCHER_DIR}/.venv"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。" >&2
  exit 1
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  python3 -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/python" -m pip install -e "${WATCHER_DIR}"

echo "Tapo C220 watcher デバッグ用の準備が完了しました。"
echo "Python: ${VENV_DIR}/bin/python"
