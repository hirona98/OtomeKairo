# Generic MCP client connector

この connector は、stdio または Streamable HTTP MCP server を OtomeKairo の `mcp.call_tool` capability として登録する。MCP server 固有の手順や依存は connector に持たない。

## 責務

- 起動時に有効な MCP server を `initialize` し、`tools/list` の catalog を hello へ載せる
- `mcp.call_tool_request` を MCP `tools/call` で実行し、result を OtomeKairo へ返す
- stdio 子 process には launcher 用 `PATH` と server 固有 `env` だけを渡す
- Streamable HTTP は TLS 検証を有効にし、設定 header を request に付与して redirect へ追従しない
- token、header、env、tool arguments を通常ログや result summary に出さない

tool の意味判断、送信前チェック、有限セッションの実行上限は OtomeKairo server が担当する。

## セットアップ

`config.example.json` は OtomeKairo への接続情報だけを含む。MCP server 定義は OtomeKairo の設定 API または Web UI で登録する。

```bash
cd connectors/mcp_client
python3 -m venv .venv
.venv/bin/pip install -e .
cp config.example.json config.local.json
```

hello payload を確認する。

```bash
.venv/bin/python -m otomekairo_mcp_client_connector --config config.local.json --print-hello
```

connector を起動する。

```bash
.venv/bin/python -m otomekairo_mcp_client_connector --config config.local.json
```

OtomeKairo access token は、`OTOMEKAIRO_ACCESS_TOKEN`、ローカル `config.db`、bootstrap の順に解決する。MCP server 設定は `GET /api/config/connectors/{client_id}/runtime-config` から取得する。

## ELYTH Remote MCP

ELYTH は他の MCP server と同じ connector と `tools/list` catalog で扱う。Streamable HTTP の設定例と有限 MCP セッションの設定 wire は [状態と設定](../../docs/design/api/状態と設定.md#put-apiconfigmcp-serversmcp_server_id) を参照する。

ELYTH API token は `headers.Authorization` に `Bearer ...` として保存する。repository、sample、通常ログ、trace に実 token を残さない。

## stdio MCP の例

e-Stat などの stdio MCP は `transport=stdio` と `command / args / cwd / env` を登録する。`transport=streamable_http` の field と混在させない。

```json
{
  "enabled": true,
  "pre_send_check_enabled": false,
  "transport": "stdio",
  "command": "uvx",
  "args": ["estat-mcp-server"],
  "cwd": null,
  "env": {
    "E_STAT_APP_ID": "..."
  }
}
```

## 接続テスト用 trace

`OTOMEKAIRO_MCP_TRACE_PATH` を指定すると、connector は OtomeKairo HTTP と event stream の送受信を JSON Lines 形式で追記する。秘密 header、env 値、MCP tool arguments は保存しない。stdio MCP の JSON-RPC を調査するときは `otomekairo-mcp-stdio-trace-proxy` を明示的に挟む。

```bash
export OTOMEKAIRO_MCP_TRACE_PATH=/tmp/otomekairo-mcp-trace.jsonl
```
