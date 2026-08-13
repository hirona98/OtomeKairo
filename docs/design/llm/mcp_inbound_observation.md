# MCP inbound 観測

MCP server が任意で持つ、届いている働きかけの有無を見る観測の正本。
設定 wire は [../api/状態と設定.md](../api/状態と設定.md)、有限 MCP セッションの開始境界は [../runtime/autonomous_run.md](../runtime/autonomous_run.md#有限-mcp-セッション) を正とする。
LLM 補助の共通境界は [LLM補助契約共通.md](LLM補助契約共通.md) に従う。

## 目的

自分から見に行く／書きに行く間隔と、向こうから届いた働きかけへの応答を分ける。

`autonomous_session.min_interval_seconds` と気にかけていることの間隔は、自分から関わる最短間隔のまま残す。
inbound 観測は、短い間隔で設定された tool を 1 回呼び、届いている働きかけがあるかだけを見る。

無いときは定期思考も有限 MCP セッションも起こさない。
あるときだけ `background_thinking` を開き、その cycle に限って対象 server の session 開始から最短間隔を外す。

これは ELYTH 固有の機能ではない。任意の MCP server に同じ意味で載せる。server id、URL、tool 名から意味を推定しない。

## 設定

MCP server 定義は `inbound_observation` を必須の exact shape として持つ。

```json
{
  "enabled": true,
  "interval_seconds": 900,
  "tool_name": "get_notifications",
  "arguments": {}
}
```

| 項目 | 役割 |
| --- | --- |
| `enabled` | この観測を行うか |
| `interval_seconds` | 同じ server をこれより頻繁には見ない |
| `tool_name` | 接続中 catalog にある tool の名前。コードが名前から意味を読まない |
| `arguments` | その tool へ渡す object。空 object を許す |

`enabled=true` は `autonomous_session.enabled=true` と `background_enabled=true` を必要とする。
`enabled=true` のとき `tool_name` は空でない文字列にする。
`enabled=false` のとき `tool_name` は空文字を許す。
`interval_seconds` は常に正の整数、`arguments` は常に object とする。

既定の `elyth` 雛形は enabled の例として `tool_name=get_notifications` と `interval_seconds=900` を持つ。MCP server 自体が disabled なら観測は走らない。
既定の `e-stat` 雛形は disabled とし、`tool_name` は空にする。

## 実行

観測は有限 MCP セッションではない。`max_tool_calls` と `min_interval_seconds` を消費しない。
観測は投稿、返信、既読化を目的にしない。変更系は今どおり有限 session の中で、個が選ぶ。

enabled かつ MCP server が enabled な定義は、次のとき due である。

- まだこの process で観測した記録が無い
- または最後に観測を試してから `interval_seconds` 以上経過している

最後に試した時刻は process-local の実行状態である。設定正本には置かない。process 再起動後は未観測として扱う。
失敗した試行も間隔を消化する。失敗を成功や未読なしへ丸めない。

due な観測は、接続中 catalog に設定した `tool_name` があるときだけ `mcp.call_tool` を 1 回呼ぶ。
catalog に無い、server が無効、connector が未接続、dispatch 失敗、tool が `is_error` を返した場合は、その server について inbound なしとし、定期思考を inbound 理由では開かない。

観測の dispatch は思考前観測と同じく `ongoing_action` を作らない。
観測は operator が設定した読み取りであり、個が選んだ外向き送信ではない。`pre_send_check` の対象にしない。

inbound 観測だけが due のとき、通常の定期思考、思考前視覚観測、`last_wake_at` の消費は行わない。
通常の定期思考または due な気にかけていることと同時なら、それらと同じ `background_thinking` に inbound を載せる。

## 意味判断

論理 role は `mcp_inbound_observation` とし、選択中の `model_preset` を使う。

LLM に任せるのは、tool 結果が「今、向こうから届いている働きかけがあるか」だけである。
返す、見る、自分から書くの選択はしない。有限 session を始めるかも決めない。

source pack は次だけを持つ。

```json
{
  "mcp_server_id": "elyth",
  "tool_name": "get_notifications",
  "is_error": false,
  "content": [],
  "structured_content": null,
  "persona_context": {}
}
```

`content` と `structured_content` は秘密値、資格情報、内部 URL を除き、判断に足る要約へ限る。
長い本文や巨大 payload をそのまま渡さない。
結果に含まれる主体の `person_ref` 化は MCP connector の `observed_persons` を正とし、この観測 role は表示名から人物を推定しない。

出力は次の exact shape とする。

```json
{
  "inbound_present": true,
  "observation_summary": "公開投稿への返信が届いている。",
  "reason_summary": "未読の返信があり、向こうからの働きかけとして扱う。"
}
```

| 項目 | 役割 |
| --- | --- |
| `inbound_present` | 届いている働きかけがあるか。コードが分岐に使う |
| `observation_summary` | 盤面へ載せる短い要約 |
| `reason_summary` | なぜそう判断したかの短い理由 |

3 キー以外を拒否する。
`observation_summary` と `reason_summary` は空でない 1 行の自然文とし、内部識別子を含めない。

`inbound_present=true` は、返信、言及、既存の私信、自分へ向けた接触のように、今関われる働きかけがある場合に使う。
空の結果、自分から見に行く材料だけ、告知や関係の変化だけで接触が無い場合は `false` にする。

契約不正は 1 回だけ repair する。再試行後も失敗した場合、その観測は失敗として閉じ、inbound なしとする。fallback で `true` にも `false` にも丸めない。

モック経路は、空でない `content` または `structured_content` があるとき `inbound_present=true`、それ以外は `false` とする。

## 判断盤面

`inbound_present=true` の観測は `workspace_context` の候補になる。`kind=inbound_observation`、`factor_ref` は `inbound_observation:<mcp_server_id>`。
`summary_text` は `observation_summary` である。定時、実行、確認せよ、を足さない。

inbound は自身の活動の比較材料である。気にかけていることのコピーでも、偽の `drive_state` でもない。
関わるか、今は返さないかは、その時点の個が決める。server が返信を義務づけない。

inbound がある cycle では autonomous family を available にしてよい。特定 capability を preferred にはしない。
inbound だけの cycle では、視覚観測や人物側の活動を inbound の代わりにしない。

## 失敗

観測入力の構築、dispatch、契約検証に失敗した場合、その server の inbound を無いものとして扱わず、失敗として記録する。
失敗した観測だけを理由に定期思考や有限 session を開かない。
失敗を `noop` の判断成功へ落とさない。

## inspection

少なくとも次を追えるようにする。

- どの MCP server の inbound 観測が due だったか
- tool call の成否
- `inbound_present`
- inbound だけの cycle だったか
- `finite_session_targets` に最短間隔を外して載ったか
