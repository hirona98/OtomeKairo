#!/usr/bin/env bash

set -euo pipefail

# microphone connector の VSCode デバッグ用 venv を準備する。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONNECTOR_DIR="${REPO_ROOT}/connectors/microphone"
VENV_DIR="${CONNECTOR_DIR}/.venv"

"${SCRIPT_DIR}/ensure_editable_install.sh" \
  "${VENV_DIR}" \
  "${CONNECTOR_DIR}" \
  "otomekairo_microphone_connector"

echo "microphone connector デバッグ用の準備が完了しました。"
echo "Python: ${VENV_DIR}/bin/python"
