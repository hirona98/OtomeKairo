#!/usr/bin/env bash

set -euo pipefail

# Tapo C220 watcher の VSCode デバッグ用 venv を準備する。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
WATCHER_DIR="${REPO_ROOT}/watchers/tapo_c220"
VENV_DIR="${WATCHER_DIR}/.venv"

"${SCRIPT_DIR}/ensure_editable_install.sh" \
  "${VENV_DIR}" \
  "${WATCHER_DIR}" \
  "otomekairo_tapo_c220_watcher"

echo "Tapo C220 watcher デバッグ用の準備が完了しました。"
echo "Python: ${VENV_DIR}/bin/python"
