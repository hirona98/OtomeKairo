#!/usr/bin/env bash

set -euo pipefail

# 単一 systemd service で動かすための実行環境を準備する。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVER_VENV_DIR="${REPO_ROOT}/.venv"
MICROPHONE_CONNECTOR_DIR="${REPO_ROOT}/connectors/microphone"
MICROPHONE_VENV_DIR="${MICROPHONE_CONNECTOR_DIR}/.venv"
TAPO_CONNECTOR_DIR="${REPO_ROOT}/connectors/tapo_c220"
TAPO_VENV_DIR="${TAPO_CONNECTOR_DIR}/.venv"
TAPO_WATCHER_DIR="${REPO_ROOT}/watchers/tapo_c220"
TAPO_WATCHER_VENV_DIR="${TAPO_WATCHER_DIR}/.venv"
MCP_CONNECTOR_DIR="${REPO_ROOT}/connectors/mcp_client"
MCP_VENV_DIR="${MCP_CONNECTOR_DIR}/.venv"
TLS_DIR="${REPO_ROOT}/var/dev-tls"
DATA_DIR="${REPO_ROOT}/var/otomekairo"
CERT_FILE="${TLS_DIR}/cert.pem"
KEY_FILE="${TLS_DIR}/key.pem"

if ! command -v openssl >/dev/null 2>&1; then
  echo "openssl が見つかりません。" >&2
  exit 1
fi

# server / connector / watcher の venv は必要なときだけ pip する
"${SCRIPT_DIR}/setup_venv.sh"
"${SCRIPT_DIR}/ensure_editable_install.sh" \
  "${MICROPHONE_VENV_DIR}" \
  "${MICROPHONE_CONNECTOR_DIR}" \
  "otomekairo_microphone_connector"
"${SCRIPT_DIR}/ensure_editable_install.sh" \
  "${TAPO_VENV_DIR}" \
  "${TAPO_CONNECTOR_DIR}" \
  "otomekairo_tapo_c220_connector"
"${SCRIPT_DIR}/ensure_editable_install.sh" \
  "${TAPO_WATCHER_VENV_DIR}" \
  "${TAPO_WATCHER_DIR}" \
  "otomekairo_tapo_c220_watcher"
"${SCRIPT_DIR}/ensure_editable_install.sh" \
  "${MCP_VENV_DIR}" \
  "${MCP_CONNECTOR_DIR}" \
  "otomekairo_mcp_client_connector"

mkdir -p "${TLS_DIR}"
mkdir -p "${DATA_DIR}"

if [[ ! -f "${CERT_FILE}" || ! -f "${KEY_FILE}" ]]; then
  openssl req \
    -x509 \
    -newkey rsa:2048 \
    -keyout "${KEY_FILE}" \
    -out "${CERT_FILE}" \
    -days 365 \
    -nodes \
    -subj "/CN=0.0.0.0"
fi

echo "service 実行環境を準備しました。"
echo "Repo   : ${REPO_ROOT}"
echo "Server : ${SERVER_VENV_DIR}/bin/python"
echo "Mic    : ${MICROPHONE_VENV_DIR}/bin/python"
echo "Tapo   : ${TAPO_VENV_DIR}/bin/python"
echo "Watcher: ${TAPO_WATCHER_VENV_DIR}/bin/python"
echo "MCP    : ${MCP_VENV_DIR}/bin/python"
echo "Data   : ${DATA_DIR}"
echo "TLS    : ${CERT_FILE}"
