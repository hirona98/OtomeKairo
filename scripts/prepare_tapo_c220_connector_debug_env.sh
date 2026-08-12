#!/usr/bin/env bash

set -euo pipefail

# パス群
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONNECTOR_DIR="${REPO_ROOT}/connectors/tapo_c220"
VENV_DIR="${CONNECTOR_DIR}/.venv"

"${SCRIPT_DIR}/ensure_editable_install.sh" \
  "${VENV_DIR}" \
  "${CONNECTOR_DIR}" \
  "otomekairo_tapo_c220_connector"

# 完了
echo "Tapo C220 connector デバッグ用の準備が完了しました。"
echo "Python: ${VENV_DIR}/bin/python"
