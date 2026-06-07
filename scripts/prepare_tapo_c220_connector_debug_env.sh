#!/usr/bin/env bash

set -euo pipefail

# パス群
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
CONNECTOR_DIR="${REPO_ROOT}/connectors/tapo_c220"
VENV_DIR="${CONNECTOR_DIR}/.venv"

# Python確認
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。" >&2
  exit 1
fi

# venv作成
if [[ ! -d "${VENV_DIR}" ]]; then
  python3 -m venv "${VENV_DIR}"
fi

# pipインストール
"${VENV_DIR}/bin/python" -m pip install -e "${CONNECTOR_DIR}"

# 完了
echo "Tapo C220 connector デバッグ用の準備が完了しました。"
echo "Python: ${VENV_DIR}/bin/python"
