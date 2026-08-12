#!/usr/bin/env bash

set -euo pipefail

# パス群
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"
TLS_DIR="${REPO_ROOT}/var/dev-tls"
DATA_DIR="${REPO_ROOT}/var/otomekairo"
CERT_FILE="${TLS_DIR}/cert.pem"
KEY_FILE="${TLS_DIR}/key.pem"

# 基本設定
"${SCRIPT_DIR}/setup_venv.sh"

# OpenSSL確認
if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl が見つかりません。" >&2
  exit 1
fi

# ディレクトリ設定
mkdir -p "${TLS_DIR}"
mkdir -p "${DATA_DIR}"

# 開発用証明書
if [[ ! -f "${CERT_FILE}" || ! -f "${KEY_FILE}" ]]; then
  openssl req \
    -x509 \
    -newkey rsa:2048 \
    -keyout "${KEY_FILE}" \
    -out "${CERT_FILE}" \
    -days 365 \
    -nodes \
    -subj "/CN=127.0.0.1"
fi

# 前回デバッグが残した listen を解放する（Address already in use 対策）。
# 失敗しても準備自体は続行し、起動時のエラーメッセージに任せる。
DEBUG_PORT="${OTOMEKAIRO_PORT:-55601}"
if ! "${SCRIPT_DIR}/free_server_port.sh" "${DEBUG_PORT}"; then
  echo "警告: ポート ${DEBUG_PORT} の自動解放に失敗しました。手動で ./scripts/free_server_port.sh ${DEBUG_PORT} --force を試すか、Windows 側で wsl --shutdown を実行してください。" >&2
fi

# 結果
echo "VSCode デバッグ用の準備が完了しました。"
echo "Python: ${VENV_DIR}/bin/python"
echo "Cert  : ${CERT_FILE}"
echo "Key   : ${KEY_FILE}"
echo "Port  : ${DEBUG_PORT}"

