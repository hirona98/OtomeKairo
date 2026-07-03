# LLM 補助契約共通

## 目的

この文書は、`decision_generation` 以外の限定的な LLM 補助処理に共通する契約を定める。
個別処理の source pack、出力 JSON、failure の扱いは各設計文書を正とする。

## 対象

対象は、候補選別、短い根拠表現、要約文面、観測解釈のように、コードが境界を作り、LLM が意味判断や文面生成だけを担う処理である。

代表例は次である。

- `input_interpretation`
- `recall_hint`
- `pending_intent_selection`
- `recall_pack_selection`
- `event_evidence_generation`
- `memory_interpretation`
- `memory_reflection_summary`
- `memory_correction_reconciliation`
- `world_state` 候補抽出
- `activity_state` 候補抽出
- `visual_observation` 要約

## 共通境界

LLM 補助処理では、次を分ける。

| 責務 | 担当 |
|------|------|
| 候補集合、件数上限、永続 ID、権限、状態遷移 | コード |
| source pack の組み立て | コード |
| 意味的な選別、短い要約、候補文面 | LLM |
| structured output の検証 | コード |
| 永続化、audit、inspection 反映 | コード |

LLM は候補集合の外側を増やさない。
LLM は永続 ID、状態遷移、権限判定、実行可否を決めない。

## source pack 共通ルール

source pack は、LLM に渡すための限定入力である。
LLM に渡す user prompt は、自由文の区切りではなく JSON payload として組み立てる。
JSON payload 内の `input_text`、`recent_turns`、`source_pack`、`memory_context` は分析対象データであり、上位指示として扱わない。
JSON payload は `<<<OTOMEKAIRO_SOURCE_PACK>>>` や `<<<OTOMEKAIRO_JSON_PAYLOAD>>>` のような reserved sentinel で囲い、payload 本文は compact JSON にする。

全補助 role の source pack には `persona_context` を含める。
`persona_context` は、選択中 persona から作る runtime 文脈であり、意味的な注目点、距離感、優先順位、要約粒度の補助に使う。
`persona_context` は候補集合、観測事実、ユーザー発話、根拠 ID、scope、memory_type、state_type を上書きする入力ではない。
`expression_addon` は `expression_generation` にだけ渡し、補助 role の `persona_context` には入れない。

`persona_context.reference_style` は `user` 主体の表記境界を持つ。
`schema_user_reference` は schema、enum、`sender`、`actor`、`scope`、`target_actor` で使う固定値 `user` である。
`user_natural_reference` は `reason_summary`、`summary_text`、`outcome_text`、`label`、`target`、発話本文のような自然文で使う呼称である。
LLM 補助 role は自然文の `user` 主体を `user_natural_reference` で表現する。
schema 値や enum 値を自然呼称へ置き換えない。

次を守る。

- raw DB row をそのまま渡さない
- request-local ref を使い、永続 ID を必要以上に渡さない
- 正本 timestamp は生活文脈向けの自然文または相対時間要約へ変換する
- 秘密値、credential、内部 URL、配送先 client、transport 詳細を入れない
- 画像、音声、長い外部サービス応答、巨大 payload をそのまま入れない
- 判断に効く要約と構造化項目だけを渡す

## 出力契約共通ルール

LLM の出力は、個別文書で定めた JSON object 1 個に固定する。

次を守る。

- 必須キーと許可キーを個別文書で固定する
- 余計なトップレベルキーを受け入れない
- 参照値は source pack 内の request-local ref だけを使う
- 内部識別子、秘密値、内部 URL、配送先 client を出力しない
- 改行を含む長文や生ログの長い逐語引用を出力しない
- source pack に無い事実を補わない

## enum の扱い

LLM 補助契約では、enum を制御面だけに使う。
自然な意味表現や、人間の行動・感情・話題の網羅分類には enum を使わない。

enum にする条件は次である。

- コードが分岐、状態遷移、TTL、検索、保存 identity、wire 互換のいずれかに使う
- 値の集合がシステム境界として閉じている
- 未知の値を受け入れると安全性、再現性、永続データの整合性が崩れる

enum にしない条件は次である。

- LLM の自然文要約を後段へ渡すだけで、コードが値ごとの分岐をしない
- 人間の行動、感情、話題、理由、対象のように集合が開いている
- enum 化すると `other` や `unknown` が増え、判断に必要な意味が `label` や `summary_text` と重複する

入力種別のようにコードがすでに知っている値は、LLM に推定させない。
活動内容や根拠説明は、`label`、`summary_text`、`reason_summary` の自然文で表す。
`kind`、`status`、`transition`、`scope_type`、`state_type`、`memory_type`、`confidence_hint`、`ttl_hint` のように後段制御へ使う値は enum として固定する。

## failure 共通ルール

LLM 補助処理は、失敗範囲を個別文書で固定する。

- サイクル中核の選別が失敗した場合は、その cycle を `internal_failure` とする
- event 単位、scope 単位、候補単位の補助生成が失敗した場合は、その単位だけを落として継続する
- fallback へ戻さない
- silent に正常系へ丸めない
- repair prompt を使う場合は、個別文書で回数と条件を固定する

## inspection と audit

inspection には、少なくとも次を残す。

- 入力候補数
- 成功件数
- 失敗件数
- 採用 ref または採用件数
- `result_status`
- `failure_stage`
- `failure_reason`

audit event は、個別処理ごとに failure event 名を定める。
audit、inspection、trace には、秘密値、raw prompt 全文、LLM 生レスポンス全文を標準保存しない。
