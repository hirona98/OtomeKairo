#!/usr/bin/env bash

set -euo pipefail

# パス群
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"

# server 用 venv に otomekairo を必要なときだけ editable install する
"${SCRIPT_DIR}/ensure_editable_install.sh" \
  "${VENV_DIR}" \
  "${REPO_ROOT}" \
  "otomekairo"

# 完了
echo "仮想環境の準備が完了しました: ${VENV_DIR}"
echo "実行: ./scripts/run_dev_server.sh"
