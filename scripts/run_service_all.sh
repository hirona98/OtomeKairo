#!/usr/bin/env bash

set -euo pipefail

# systemd から 1 プロセスとして起動され、本体と connector 群を同じ lifecycle で扱う。
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
SERVER_VENV_DIR="${REPO_ROOT}/.venv"
TAPO_CONNECTOR_DIR="${REPO_ROOT}/connectors/tapo_c220"
TAPO_VENV_DIR="${TAPO_CONNECTOR_DIR}/.venv"
TAPO_CONFIG_FILE="${TAPO_CONNECTOR_DIR}/config.local.json"
MCP_CONNECTOR_DIR="${REPO_ROOT}/connectors/mcp_client"
MCP_VENV_DIR="${MCP_CONNECTOR_DIR}/.venv"
MCP_CONFIG_FILE="${MCP_CONNECTOR_DIR}/config.local.json"
TLS_DIR="${REPO_ROOT}/var/dev-tls"
DATA_DIR="${REPO_ROOT}/var/otomekairo"
CERT_FILE="${TLS_DIR}/cert.pem"
KEY_FILE="${TLS_DIR}/key.pem"
SERVER_HOST="${OTOMEKAIRO_HOST:-0.0.0.0}"
SERVER_PORT="${OTOMEKAIRO_PORT:-55601}"
CONNECTOR_SERVER_URL="${OTOMEKAIRO_SERVER_URL:-https://127.0.0.1:${SERVER_PORT}}"

SERVER_PID=""
TAPO_PID=""
MCP_PID=""

require_executable() {
  local path="$1"
  local label="$2"

  if [[ ! -x "${path}" ]]; then
    echo "${label} が見つかりません: ${path}" >&2
    echo "先に ./scripts/prepare_service_env.sh を実行してください。" >&2
    exit 1
  fi
}

cleanup() {
  local status="${1:-0}"
  local pid

  trap - EXIT INT TERM

  for pid in "${MCP_PID}" "${TAPO_PID}" "${SERVER_PID}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" >/dev/null 2>&1; then
      kill "${pid}" >/dev/null 2>&1 || true
    fi
  done

  for pid in "${MCP_PID}" "${TAPO_PID}" "${SERVER_PID}"; do
    if [[ -n "${pid}" ]]; then
      wait "${pid}" >/dev/null 2>&1 || true
    fi
  done

  exit "${status}"
}

wait_for_server() {
  "${SERVER_VENV_DIR}/bin/python" - "${SERVER_PORT}" <<'PY'
import socket
import sys
import time

port = int(sys.argv[1])
deadline = time.monotonic() + 60.0

while True:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0):
            raise SystemExit(0)
    except OSError:
        if time.monotonic() >= deadline:
            print(f"server の起動待ちが timeout しました: 127.0.0.1:{port}", file=sys.stderr)
            raise SystemExit(1)
        time.sleep(0.2)
PY
}

trap 'cleanup 143' INT TERM
trap 'cleanup $?' EXIT

require_executable "${SERVER_VENV_DIR}/bin/python" "server venv"
require_executable "${TAPO_VENV_DIR}/bin/python" "Tapo connector venv"
require_executable "${MCP_VENV_DIR}/bin/python" "MCP connector venv"

if [[ ! -f "${CERT_FILE}" || ! -f "${KEY_FILE}" ]]; then
  echo "TLS 証明書が見つかりません。先に ./scripts/prepare_service_env.sh を実行してください。" >&2
  exit 1
fi

mkdir -p "${DATA_DIR}"

export OTOMEKAIRO_HOST="${SERVER_HOST}"
export OTOMEKAIRO_PORT="${SERVER_PORT}"
export OTOMEKAIRO_TLS_CERT_FILE="${CERT_FILE}"
export OTOMEKAIRO_TLS_KEY_FILE="${KEY_FILE}"
export OTOMEKAIRO_DATA_DIR="${DATA_DIR}"
export PYTHONPATH="${REPO_ROOT}/src"

echo "starting OtomeKairo server on ${SERVER_HOST}:${SERVER_PORT}" >&2
"${SERVER_VENV_DIR}/bin/python" -m otomekairo.run &
SERVER_PID="$!"

wait_for_server

export OTOMEKAIRO_SERVER_URL="${CONNECTOR_SERVER_URL}"

tapo_args=()
if [[ -f "${TAPO_CONFIG_FILE}" ]]; then
  tapo_args=(--config "${TAPO_CONFIG_FILE}")
fi

mcp_args=()
if [[ -f "${MCP_CONFIG_FILE}" ]]; then
  mcp_args=(--config "${MCP_CONFIG_FILE}")
fi

echo "starting Tapo C220 connector" >&2
"${TAPO_VENV_DIR}/bin/python" -m otomekairo_tapo_c220_connector "${tapo_args[@]}" &
TAPO_PID="$!"

echo "starting MCP client connector" >&2
"${MCP_VENV_DIR}/bin/python" -m otomekairo_mcp_client_connector "${mcp_args[@]}" &
MCP_PID="$!"

set +e
wait -n "${SERVER_PID}" "${TAPO_PID}" "${MCP_PID}"
status="$?"
set -e
echo "OtomeKairo child process exited: status=${status}" >&2
cleanup "${status}"
