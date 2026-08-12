#!/usr/bin/env bash

set -euo pipefail

# 指定ポートを listen しているプロセスを解放する。
# 既定では otomekairo.run だけを止める。--force でそのポートの listen をすべて止める。
# WSL2 では、プロセスが消えてもホスト側にポートが残ることがある。その場合は
# Windows 側で `wsl --shutdown` を実行する（PC 再起動より軽い）。

usage() {
  cat <<'EOF' >&2
使い方: free_server_port.sh [port] [--force] [--dry-run]

  port     解放する TCP ポート（省略時: OTOMEKAIRO_PORT または 55601）
  --force  otomekairo 以外も含め、そのポートの LISTEN を止める
  --dry-run 実際には kill せず対象だけ表示する
EOF
}

PORT="${OTOMEKAIRO_PORT:-55601}"
FORCE=0
DRY_RUN=0

for arg in "$@"; do
  case "${arg}" in
    -h|--help)
      usage
      exit 0
      ;;
    --force)
      FORCE=1
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    *)
      if [[ "${arg}" =~ ^[0-9]+$ ]]; then
        PORT="${arg}"
      else
        echo "不明な引数: ${arg}" >&2
        usage
        exit 2
      fi
      ;;
  esac
done

if ! [[ "${PORT}" =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "不正なポート: ${PORT}" >&2
  exit 2
fi

# LISTEN 中 PID を収集する（ss / fuser / lsof の順で試す）。
collect_listen_pids() {
  local port="$1"
  local pids=""
  local line pid

  if command -v ss >/dev/null 2>&1; then
    while IFS= read -r line; do
      if [[ "${line}" =~ pid=([0-9]+) ]]; then
        pids+="${BASH_REMATCH[1]}"$'\n'
      fi
    done < <(ss -tlnp 2>/dev/null | grep -E ":${port}\\b" || true)
  fi

  if command -v fuser >/dev/null 2>&1; then
    # fuser は stderr に注釈、stdout に PID を出す。
    pids+="$(fuser -n tcp "${port}" 2>/dev/null || true)"$'\n'
  fi

  if command -v lsof >/dev/null 2>&1; then
    while IFS= read -r pid; do
      [[ -n "${pid}" ]] && pids+="${pid}"$'\n'
    done < <(lsof -t -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)
  fi

  # 重複除去
  printf '%s\n' ${pids} | awk 'NF && !seen[$0]++'
}

cmdline_of() {
  local pid="$1"
  if [[ -r "/proc/${pid}/cmdline" ]]; then
    tr '\0' ' ' <"/proc/${pid}/cmdline"
    return 0
  fi
  ps -p "${pid}" -o args= 2>/dev/null || true
}

is_otomekairo_server() {
  local cmd="$1"
  [[ "${cmd}" == *otomekairo.run* ]] || [[ "${cmd}" == *otomekairo/run.py* ]] || [[ "${cmd}" == *" -m otomekairo"* ]]
}

PIDS="$(collect_listen_pids "${PORT}")"
if [[ -z "${PIDS}" ]]; then
  echo "ポート ${PORT} を LISTEN しているプロセスはありません。"
  exit 0
fi

echo "ポート ${PORT} の LISTEN プロセス:"
TARGETS=()
while IFS= read -r pid; do
  [[ -z "${pid}" ]] && continue
  cmd="$(cmdline_of "${pid}")"
  echo "  pid=${pid} cmd=${cmd}"
  if (( FORCE == 1 )) || is_otomekairo_server "${cmd}"; then
    TARGETS+=("${pid}")
  else
    echo "    -> otomekairo 以外のためスキップ（止めるなら --force）"
  fi
done <<<"${PIDS}"

if (( ${#TARGETS[@]} == 0 )); then
  echo "止める対象がありません。" >&2
  echo "WSL2 でプロセスが無いのに bind できない場合は、Windows 側で次を実行してください:" >&2
  echo "  wsl --shutdown" >&2
  exit 1
fi

if (( DRY_RUN == 1 )); then
  echo "dry-run: 次の PID を停止予定: ${TARGETS[*]}"
  exit 0
fi

for pid in "${TARGETS[@]}"; do
  echo "停止中: pid=${pid}"
  kill "${pid}" 2>/dev/null || true
done

# 終了待ち
for _ in 1 2 3 4 5 6 7 8 9 10; do
  remaining="$(collect_listen_pids "${PORT}")"
  [[ -z "${remaining}" ]] && break
  sleep 0.2
done

remaining="$(collect_listen_pids "${PORT}")"
if [[ -n "${remaining}" ]]; then
  echo "まだ残っているため SIGKILL します: ${remaining}"
  for pid in ${remaining}; do
    kill -9 "${pid}" 2>/dev/null || true
  done
  sleep 0.3
fi

remaining="$(collect_listen_pids "${PORT}")"
if [[ -n "${remaining}" ]]; then
  echo "ポート ${PORT} を解放できませんでした。残: ${remaining}" >&2
  echo "WSL2 なら Windows 側で wsl --shutdown を試してください。" >&2
  exit 1
fi

echo "ポート ${PORT} を解放しました。"
