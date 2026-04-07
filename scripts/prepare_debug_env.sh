#!/usr/bin/env bash

set -euo pipefail

# Paths
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"
TLS_DIR="${REPO_ROOT}/var/dev-tls"
DATA_DIR="${REPO_ROOT}/var/otomekairo"
CERT_FILE="${TLS_DIR}/cert.pem"
KEY_FILE="${TLS_DIR}/key.pem"

# BaseSetup
"${SCRIPT_DIR}/setup_venv.sh"

# OpenSSLCheck
if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl が見つかりません。" >&2
  exit 1
fi

# DirectorySetup
mkdir -p "${TLS_DIR}"
mkdir -p "${DATA_DIR}"

# DevCertificate
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

# Result
echo "VSCode デバッグ用の準備が完了しました。"
echo "Python: ${VENV_DIR}/bin/python"
echo "Cert  : ${CERT_FILE}"
echo "Key   : ${KEY_FILE}"
