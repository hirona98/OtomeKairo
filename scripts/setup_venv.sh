#!/usr/bin/env bash

set -euo pipefail

# パス群
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"

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
"${VENV_DIR}/bin/python" -m pip install -e "${REPO_ROOT}"

# 完了
echo "仮想環境を作成しました: ${VENV_DIR}"
echo "実行: ./scripts/run_dev_server.sh"
