#!/usr/bin/env bash

set -euo pipefail

# microphone connector の VSCode デバッグ用 venv を準備する。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONNECTOR_DIR="${REPO_ROOT}/connectors/microphone"
VENV_DIR="${CONNECTOR_DIR}/.venv"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。" >&2
  exit 1
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  python3 -m venv "${VENV_DIR}"
fi

"${VENV_DIR}/bin/python" -m pip install -e "${CONNECTOR_DIR}"

echo "microphone connector デバッグ用の準備が完了しました。"
echo "Python: ${VENV_DIR}/bin/python"
