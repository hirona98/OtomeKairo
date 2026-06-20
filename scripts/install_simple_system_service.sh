#!/usr/bin/env bash

set -euo pipefail

# /opt/OtomeKairo に置いた repository を単一 systemd service として登録する。
EXPECTED_REPO_ROOT="/opt/OtomeKairo"
SERVICE_NAME="otomekairo.service"
SERVICE_USER="${OTOMEKAIRO_SERVICE_USER:-hirona}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ "${EUID}" -ne 0 ]]; then
  echo "root 権限で実行してください: sudo ${BASH_SOURCE[0]}" >&2
  exit 1
fi

if [[ "${REPO_ROOT}" != "${EXPECTED_REPO_ROOT}" ]]; then
  echo "この簡易 service は ${EXPECTED_REPO_ROOT} 固定です。" >&2
  echo "現在の場所: ${REPO_ROOT}" >&2
  exit 1
fi

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  echo "実行ユーザーが存在しません: ${SERVICE_USER}" >&2
  exit 1
fi

chown -R "${SERVICE_USER}:${SERVICE_USER}" "${EXPECTED_REPO_ROOT}"
runuser -u "${SERVICE_USER}" -- "${EXPECTED_REPO_ROOT}/scripts/prepare_service_env.sh"

unit_file="$(mktemp)"
cat >"${unit_file}" <<EOF
[Unit]
Description=OtomeKairo
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
WorkingDirectory=${EXPECTED_REPO_ROOT}
Environment=PYTHONUNBUFFERED=1
Environment=PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
ExecStart=${EXPECTED_REPO_ROOT}/scripts/run_service_all.sh
Restart=always
RestartSec=3
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

install -m 0644 "${unit_file}" "/etc/systemd/system/${SERVICE_NAME}"
rm -f "${unit_file}"

systemctl daemon-reload

echo "systemd service を登録しました: /etc/systemd/system/${SERVICE_NAME}"
echo "起動: sudo systemctl enable --now otomekairo"
echo "状態: sudo systemctl status otomekairo"
echo "ログ: sudo journalctl -u otomekairo -f"
