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
DEFAULT_PORT="55601"

# venv確認
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo ".venv がありません。先に ./scripts/setup_venv.sh を実行してください。" >&2
  exit 1
fi

# OpenSSL確認
if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl が見つかりません。" >&2
  exit 1
fi

# ディレクトリ設定
mkdir -p "${TLS_DIR}"
mkdir -p "${DATA_DIR}"

# ポート確認
if command -v ss >/dev/null 2>&1; then
  if ss -ltn | grep -q ":${OTOMEKAIRO_PORT:-${DEFAULT_PORT}}\\b"; then
    echo "ポート ${OTOMEKAIRO_PORT:-${DEFAULT_PORT}} は使用中です。既存プロセスを止めるか、OTOMEKAIRO_PORT を変更してください。" >&2
    exit 1
  fi
fi

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

# サーバー実行
export OTOMEKAIRO_HOST="${OTOMEKAIRO_HOST:-127.0.0.1}"
export OTOMEKAIRO_PORT="${OTOMEKAIRO_PORT:-${DEFAULT_PORT}}"
export OTOMEKAIRO_TLS_CERT_FILE="${OTOMEKAIRO_TLS_CERT_FILE:-${CERT_FILE}}"
export OTOMEKAIRO_TLS_KEY_FILE="${OTOMEKAIRO_TLS_KEY_FILE:-${KEY_FILE}}"
export OTOMEKAIRO_DATA_DIR="${OTOMEKAIRO_DATA_DIR:-${DATA_DIR}}"
export PYTHONPATH="${REPO_ROOT}/src"

"${VENV_DIR}/bin/python" -m otomekairo.run
