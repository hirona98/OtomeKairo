# LLM 補助契約共通

## 目的

この文書は、`decision_generation` 以外の限定的な LLM 補助処理に共通する契約を定める。
個別処理の source pack、出力 JSON、failure の扱いは各設計文書を正とする。

## 対象

対象は、候補選別、短い根拠表現、要約文面、観測解釈のように、コードが境界を作り、LLM が意味判断や文面生成だけを担う処理である。

代表例は次である。

- `pending_intent_selection`
- `recall_pack_selection`
- `event_evidence_generation`
- `memory_reflection_summary`
- `world_state` 候補抽出

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

## failure 共通ルール

LLM 補助処理は、失敗範囲を個別文書で固定する。

- サイクル中核の選別が失敗した場合は、その cycle を `internal_failure` とする
- event 単位、scope 単位、候補単位の補助生成が失敗した場合は、その単位だけを落として継続する
- 旧ロジック fallback へ戻さない
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
