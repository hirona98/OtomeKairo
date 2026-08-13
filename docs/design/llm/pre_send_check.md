# 送信前チェック

## 目的

この文書は、OtomeKairo が外部 MCP tool を実行する前に行う `pre_send_check` の意味境界と LLM 補助契約を正本にする。

設定 wire は [../api/状態と設定.md](../api/状態と設定.md)、event wire は [../api/event_stream.md](../api/event_stream.md)、dispatch 順序は [../api/実行連携.md](../api/実行連携.md) を正とする。

`pre_send_check` は、外部サービスへ送る最終 `arguments` に、現実人物の私的情報、資格情報、第三者との私的会話、精密な位置や予定、健康、金銭、法務、非公開観測などが含まれないかを意味判定する。
対人発話の `disclosure_review` とは role、入力、出力契約を分ける。

## 適用境界

- 初期適用先は `mcp.call_tool` とする
- MCP server 定義の `pre_send_check_enabled=true` の場合、読み取りを含む全 tool call を対象にする
- 読み取り中心の MCP（既定の e-stat など）は `pre_send_check_enabled=false` を既定とする
- 投稿や外部書き込みを含む MCP は `true` にする。既定の ELYTH 設定例はこの方針で `true` とするが、server id や URL による特別な強制は行わない
- tool 名、description、argument key の固定一覧からチェック要否を推定しない
- ELYTH 固有の capability、connector、MCP fork を作らない
- connector と外部 MCP server はチェック済み request を実行するだけとする

チェックは server 本体で、manifest、権限、binding、接続中 catalog、tool `inputSchema` を検証した後、request record、`ongoing_action`、stream event を作る前に行う。
通常判断、capability result follow-up、`autonomous_run` のいずれから発生した request も同じ境界を通す。
MCP inbound 観測の tool call は operator が設定した読み取りであり、個が選んだ外向き送信ではない。`pre_send_check` の対象にしない。境界は [mcp_inbound_observation.md](mcp_inbound_observation.md) を正とする。

## ローカル秘密値検査

チェック用 LLM へ渡す前に、コードは設定正本にある既知の秘密値と、outgoing `arguments` 内の string 値を実値照合する。
対象は token、API key、password、および秘密値として保持する MCP env 値とする。

照合に使う秘密値は、長さ 12 文字以上のものに限定する。
短い設定値は日常語や一般文と衝突しやすいため、局所照合の対象にしない。
長さ 12 文字未満の既知秘密が含まれていても、この段階では止めず、後段の LLM 審査に委ねる。

既知の秘密値（最小長以上）が string の一部に現れた場合は LLM を呼ばず `withhold` とする。
これは資格情報という構造化済み値の機械的照合であり、自然文の意味判定を文字列規則へ置き換えるものではない。

## LLM 入力

user prompt は JSON payload 1 個とし、[LLM補助契約共通.md](LLM補助契約共通.md) の sentinel と data 境界に従う。

入力は次に限定する。

```json
{
  "channel": {
    "capability_id": "mcp.call_tool",
    "mcp_server_id": "elyth",
    "tool_name": "create_post"
  },
  "tool": {
    "name": "create_post",
    "description": "新しい投稿を作成する",
    "input_schema": { "type": "object" }
  },
  "arguments": {
    "content": "投稿候補"
  }
}
```

- `tool` と `arguments` は外部由来の分析対象データであり、上位指示として扱わない
- `arguments` は実際に外部へ送る最終値を全体で渡す
- `persona_context`、RecallPack、現在入力、人物文脈、判断理由を追加しない
- credential、MCP env、内部 URL、配送先 client、transport 詳細を追加しない
- payload を文字列長で切らない。モデル入力として処理できない場合はチェック失敗とする

## LLM 出力契約

出力は次の 2 key だけを持つ JSON object とする。

```json
{
  "outcome": "allow",
  "reason_summary": "外部送信してよい一般的な内容である。"
}
```

- `outcome` は `allow / withhold` のいずれかとする
- `reason_summary` は非空の短い自然文とし、candidate や秘密値を再掲しない
- `allow` は元の `arguments` を変更せず dispatch してよいことを表す
- `withhold` は当該 request を dispatch しないことを表す
- rewrite、field patch、arguments 再生成はこの role で行わない

契約検証に失敗した場合だけ repair を 1 回行う。
repair 後も不正、transport error、timeout の場合はチェック失敗とし、未チェック request を送らない。
mock 実行は暗黙の `allow` にせず、test double から結果を明示注入する。

## 判定方針

次の情報を外部サービスへ送らない。

- API key、token、password、秘密の内部識別子
- 現実人物を特定する非公開の住所、連絡先、アカウント情報
- 不要に精密な現在位置、行動予定、生活パターン
- 健康、金銭、法務に関する非公開情報
- 第三者との私的会話や非公開エピソードの横流し
- private な画面、カメラ、workspace から得た非公開観測

個自身の一般的な感想、公開情報、公開識別子を外部操作の対象として使うことは許可できる。
公開可否が不明な現実人物の情報は安全側に判定する。
ユーザーが送信を明示していても、上記のセンシティブ情報は許可しない。

## withhold と再判断

最初の candidate が `withhold` になった場合、候補本文と `reason_summary` を渡さず、次の固定 feedback で同一 cycle の判断を 1 回だけ再生成する。

> 前の MCP request は送信前チェックで見送られた。同じ目的の安全な別案または noop を選ぶ。

再判断結果が MCP request なら再度チェックする。
2 回目も `withhold`、または再判断が `noop` の場合は外部送信なしで終了する。
チェック生成自体の失敗では candidate を再生成せず、cycle を `internal_failure` とする。

`autonomous_run` の step では同じ一回制限を使い、固定 feedback は安全な `capability_request` または `action.kind=none` を求める。

通常の判断 cycle と `autonomous_run` では、terminal 後の終了単位が異なる。

| 経路 | 最初の withhold 後 | 2 回目 withhold | チェック失敗 |
|------|-------------------|-----------------|--------------|
| 通常 cycle | 固定 feedback で 1 回再判断 | 外部送信なしで cycle を `noop` 相当に確定 | cycle を `internal_failure` |
| `autonomous_run` | 固定 feedback で step を 1 回再生成 | 外部送信なしで **当該 run を `cancelled`** | 外部送信なしで **当該 run を `cancelled`** |

再生成が `action.kind=none` の場合は外部送信だけ見送り、run 自体の遷移は step 契約（`transition`）に従う。
安全な別 action（別の `capability_request` や `speech`）が成立した場合も、最初の `withhold` を本文なしの audit event として残す。
2 回目 withhold とチェック失敗で run を cancel するのは、未審査・不安全な外部作用を自律継続へ残さないための fail-closed である。

terminal な `withhold` またはチェック失敗は候補本文を含まない `system_notice` で通知する。
起点人物がいる場合は同じ定型通知を会話欄の system message として表示し、assistant 発話や音声にはしない。

## audit と inspection

残してよい情報は次に限定する。

- `mcp_server_id / tool_name`
- `result_status`
- `outcome`
- コードが生成した理由 code
- review attempt 数
- `failure_stage / failure_reason`

candidate `arguments`、`reason_summary`、raw prompt、LLM 生 response、秘密値を保存しない。
最初の `withhold` 後に安全な request が成功した場合も、本文を持たない attempt 要約だけを inspection へ残す。
