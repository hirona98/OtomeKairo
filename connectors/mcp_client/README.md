# Generic MCP client connector

この connector は、stdio MCP server を OtomeKairo の `mcp.call_tool` capability として登録する。
OtomeKairo server 本体へ MCP server 固有依存を入れない。

## 責務

- 起動時に設定済み MCP server を `initialize` し、`tools/list` のうち `enabled_tools` に含まれる tool だけを hello の `mcp_servers` へ載せる
- 設定済み `enabled_tools` に含まれる `mcp.call_tool_request` だけを MCP server の `tools/call` で実行する
- `POST /api/capability/result` へ result を返す
- MCP API key、token、内部 URL の秘密部分を通常ログや result に出さない

## e-Stat 設定例

`config.example.json` は OtomeKairo への接続情報だけを含む。
MCP server 定義（e-Stat など）は OtomeKairo 本体の設定 API に登録する。
e-Stat のアプリケーション ID は [e-Stat API](https://www.e-stat.go.jp/api/) で発行し、`E_STAT_APP_ID` に設定する。

```bash
cd connectors/mcp_client
python3 -m venv .venv
.venv/bin/pip install -e .
cp config.example.json config.local.json
```

e-Stat を登録する。

```bash
curl -k \
  -H "Authorization: Bearer $OTOMEKAIRO_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -X PUT \
  https://127.0.0.1:55601/api/config/mcp-servers/e-stat \
  -d '{
    "enabled": true,
    "command": "uvx",
    "args": ["estat-mcp-server"],
    "cwd": null,
    "enabled_tools": ["search_e_stat_tables", "get_e_stat_data_catalog"],
    "env": {
      "E_STAT_APP_ID": "..."
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
実 token と `E_STAT_APP_ID` を repository、sample、通常ログ、result に保存しない。

## 接続テスト用 trace

`OTOMEKAIRO_MCP_TRACE_PATH` を指定すると、connector は送受信内容を JSON Lines 形式で追記する。
trace には `Authorization`、`x-api-key`、`E_STAT_APP_ID`、token、password、secret をマスクして保存する。

```bash
export OTOMEKAIRO_MCP_TRACE_PATH=/tmp/otomekairo-mcp-trace.jsonl
```

完全ローカルで OtomeKairo と MCP connector の経路だけを確認する場合は、同梱の偽 MCP server を登録する。

```bash
curl -k \
  -H "Authorization: Bearer $OTOMEKAIRO_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -X PUT \
  https://127.0.0.1:55601/api/config/mcp-servers/mcp-path-test \
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

trace file には次の境界が記録される。

- `otomekairo_http`: connector と OtomeKairo HTTP API の request / response
- `otomekairo_event`: event stream の hello、request、result
- `mcp_stdio`: connector と MCP server 間の JSON-RPC
