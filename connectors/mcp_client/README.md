# Generic MCP client connector

この connector は、stdio MCP server を OtomeKairo の `mcp.call_tool` capability として登録する。
OtomeKairo server 本体へ MCP server 固有依存を入れない。

## 責務

- 起動時に設定済み MCP server を `initialize` し、`tools/list` のうち `enabled_tools` に含まれる tool だけを hello の `mcp_servers` へ載せる
- 設定済み `enabled_tools` に含まれる `mcp.call_tool_request` だけを MCP server の `tools/call` で実行する
- `POST /api/capability/result` へ result を返す
- MCP API key、token、内部 URL の秘密部分を通常ログや result に出さない

## ELYTH 設定例

`config.example.json` は OtomeKairo への接続情報だけを含む。
ELYTH の MCP server 定義は OtomeKairo 本体の設定 API に登録する。

```bash
cd connectors/mcp_client
python3 -m venv .venv
.venv/bin/pip install -e .
cp config.example.json config.local.json
```

ELYTH を登録する。

```bash
curl -k \
  -H "Authorization: Bearer $OTOMEKAIRO_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -X PUT \
  https://127.0.0.1:55601/api/config/mcp-servers/elyth \
  -d '{
    "enabled": true,
    "command": "npx",
    "args": ["-y", "elyth-mcp-server@latest"],
    "cwd": null,
    "enabled_tools": ["get_information", "create_post"],
    "env": {
      "ELYTH_API_BASE": "https://elythworld.com",
      "ELYTH_API_KEY": "..."
    }
  }'
```

hello payload を確認する。

```bash
.venv/bin/python -m otomekairo_mcp_client_connector --config config.local.json --print-hello
```

connector を起動する。

```bash
.venv/bin/python -m otomekairo_mcp_client_connector --config config.local.json
```

OtomeKairo access token は、`OTOMEKAIRO_ACCESS_TOKEN`、ローカル `config.db`、bootstrap の順に解決する。
MCP server 設定は `GET /api/config/connectors/{client_id}/runtime-config` から取得する。
実 token と ELYTH API key を repository、sample、通常ログ、result に保存しない。

## ELYTH 接続テスト用 trace

`OTOMEKAIRO_MCP_TRACE_PATH` を指定すると、connector は送受信内容を JSON Lines 形式で追記する。
trace には `Authorization`、`x-api-key`、`ELYTH_API_KEY`、token、password、secret をマスクして保存する。

```bash
export OTOMEKAIRO_MCP_TRACE_PATH=/tmp/otomekairo-elyth-trace.jsonl
```

完全ローカルで OtomeKairo と MCP connector の経路だけを確認する場合は、偽 ELYTH MCP server を登録する。

```bash
curl -k \
  -H "Authorization: Bearer $OTOMEKAIRO_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -X PUT \
  https://127.0.0.1:55601/api/config/mcp-servers/elyth-test \
  -d '{
    "enabled": true,
    "command": "python3",
    "args": ["-m", "otomekairo_mcp_client_connector.elyth_fake_mcp_server"],
    "cwd": null,
    "enabled_tools": ["get_information", "create_post"],
    "env": {
      "PYTHONPATH": "connectors/mcp_client/src"
    }
  }'
```

本物の `elyth-mcp-server@latest` が ELYTH API へ送る HTTP request を受信だけで確認する場合は、ローカル recorder を起動する。

```bash
.venv/bin/otomekairo-elyth-http-recorder --host 127.0.0.1 --port 18080
```

別 terminal で、ELYTH MCP server を stdio trace proxy 経由にして登録する。

```bash
curl -k \
  -H "Authorization: Bearer $OTOMEKAIRO_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -X PUT \
  https://127.0.0.1:55601/api/config/mcp-servers/elyth-test \
  -d '{
    "enabled": true,
    "command": "otomekairo-mcp-stdio-trace-proxy",
    "args": ["--", "npx", "-y", "elyth-mcp-server@latest"],
    "cwd": null,
    "enabled_tools": ["get_information", "create_post"],
    "env": {
      "ELYTH_API_BASE": "http://127.0.0.1:18080",
      "ELYTH_API_KEY": "local-recorder-key"
    }
  }'
```

この設定では ELYTH API base が `127.0.0.1` を指すため、ELYTH 本体へ HTTP request を送らない。
trace file には次の境界が記録される。

- `otomekairo_http`: connector と OtomeKairo HTTP API の request / response
- `otomekairo_event`: event stream の hello、request、result
- `mcp_stdio`: connector と MCP server 間の JSON-RPC
- `elyth_http`: ELYTH API 相当の HTTP request / response
