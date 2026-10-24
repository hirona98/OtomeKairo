#!/usr/bin/env bash

set -euo pipefail
set +x

usage() {
  printf '%s\n' \
    'Usage: CocoroNotify.sh [--config FILE] [--] FROM MESSAGE' \
    'Example: ~/CocoroNotify.sh Codex "修正が終了したと伝えてほしい。"' \
    'Requires: Bash and curl (Python / jq are not used).'
}

fail() {
  printf 'CocoroNotify.sh: %s\n' "$1" >&2
  exit 1
}

json_escape() {
  local value="$1" code control escaped
  value=${value//\\/\\\\}
  value=${value//\"/\\\"}
  for ((code = 1; code < 32; code++)); do
    printf -v control '%b' "\\$(printf '%03o' "$code")"
    printf -v escaped '\\u%04x' "$code"
    value=${value//"$control"/"$escaped"}
  done
  printf '%s' "$value"
}

config_file="${HOME}/.config/otomekairo/notify.conf"
while (($#)); do
  case "$1" in
    --help|-h) usage; exit 0 ;;
    --config)
      (($# >= 2)) || fail '--config に設定ファイルを指定してください。'
      config_file="$2"
      shift 2
      ;;
    --) shift; break ;;
    -*) fail "未対応のオプションです: $1" ;;
    *) break ;;
  esac
done

(($# == 2)) || { usage >&2; exit 1; }
notify_source="$1"
notify_message="$2"
[[ "$notify_source" =~ [^[:space:]] ]] || fail 'FROM は空にできません。'
[[ "$notify_message" =~ [^[:space:]] ]] || fail 'MESSAGE は空にできません。'
[[ -r "$config_file" ]] || fail "設定ファイルを読めません: $config_file"
command -v curl >/dev/null 2>&1 || fail 'curl が見つかりません。'

OTOMEKAIRO_URL='https://cocoro-box:55601'
OTOMEKAIRO_TOKEN=''
OTOMEKAIRO_TLS_VERIFY=true
OTOMEKAIRO_CA_FILE=''
OTOMEKAIRO_TIMEOUT_SECONDS=300
# 利用者自身が保存した Bash 設定を読む。
source "$config_file"
set +x

[[ "$OTOMEKAIRO_URL" == https://* ]] || fail 'OTOMEKAIRO_URL に HTTPS の接続先を設定してください。'
[[ -n "$OTOMEKAIRO_TOKEN" && "$OTOMEKAIRO_TOKEN" != *$'\n'* && "$OTOMEKAIRO_TOKEN" != *$'\r'* ]] \
  || fail 'OTOMEKAIRO_TOKEN に有効な Bearer token を設定してください。'
[[ "$OTOMEKAIRO_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]] \
  || fail 'OTOMEKAIRO_TIMEOUT_SECONDS は正の整数で指定してください。'

curl_options=(
  --disable --silent --show-error --fail-with-body --proto '=https'
  --connect-timeout 5 --max-time "$OTOMEKAIRO_TIMEOUT_SECONDS"
  --request POST --header 'Content-Type: application/json; charset=utf-8'
  --header @-
)
case "$OTOMEKAIRO_TLS_VERIFY" in
  true)
    if [[ -n "$OTOMEKAIRO_CA_FILE" ]]; then
      [[ -r "$OTOMEKAIRO_CA_FILE" ]] || fail 'OTOMEKAIRO_CA_FILE を読めません。'
      curl_options+=(--cacert "$OTOMEKAIRO_CA_FILE")
    fi
    ;;
  false)
    [[ -z "$OTOMEKAIRO_CA_FILE" ]] || fail 'TLS検証無効とCAファイルは同時に設定できません。'
    curl_options+=(--insecure)
    ;;
  *) fail 'OTOMEKAIRO_TLS_VERIFY は true / false で指定してください。' ;;
esac

printf -v request_body '{"message":"%s","client_context":{"source":"%s"}}' \
  "$(json_escape "$notify_message")" "$(json_escape "$notify_source")"
if response=$(printf 'Authorization: Bearer %s\n' "$OTOMEKAIRO_TOKEN" \
  | curl "${curl_options[@]}" --data-binary "$request_body" --url "${OTOMEKAIRO_URL%/}/api/wake"); then
  printf '%s\n' "$response"
else
  curl_status=$?
  [[ -z "$response" ]] || printf '%s\n' "$response" >&2
  printf 'CocoroNotify.sh: HTTP送信に失敗しました (curl=%s)。自動再送は行いません。\n' "$curl_status" >&2
  exit "$curl_status"
fi

# OtomeKairo が返す共通 envelope と閉じた result_kind を判定する。
envelope_pattern='^[[:space:]]*\{[[:space:]]*"ok"[[:space:]]*:[[:space:]]*true[[:space:]]*,[[:space:]]*"data"[[:space:]]*:[[:space:]]*\{'
result_pattern='"result_kind"[[:space:]]*:[[:space:]]*"([^"]+)"'
[[ "$response" =~ $envelope_pattern && "$response" =~ $result_pattern ]] \
  || fail 'OtomeKairo の正常な判断結果を確認できません。'
case "${BASH_REMATCH[1]}" in
  speech|noop|capability_request) ;;
  internal_failure) fail 'OtomeKairo の判断処理が失敗しました。' ;;
  *) fail 'OtomeKairo が未対応の判断結果を返しました。' ;;
esac
