# LLM 構造化出力 transport

## 目的

この文書は、構造化 JSON を返す LLM 呼び出しの transport 契約を定める。
role ごとのキー、enum、意味検証は各設計文書とコードの validator を正とする。
model_preset の項目意味は [../configuration/モデルプリセット詳細.md](../configuration/モデルプリセット詳細.md) を正とする。

## 対象

`_generate_structured_payload` を通る生成は、すべてこの transport を使う。

対象外は次である。

- `expression_generation`（`generate_speech`）。自由文の発話本文を返す
- embedding
- `model` が `mock` で始まる開発用経路。LiteLLM を通さない

## 要求の形

structured 呼び出しは LiteLLM の `completion` に次を付ける。

```json
{
  "type": "json_schema",
  "json_schema": {
    "name": "recall_pack_selection",
    "strict": true,
    "schema": {}
  }
}
```

`name` は operation 名を `[A-Za-z0-9_]` に正規化した値である。
`schema` は Gemini の JSON Schema subset で書く。

schema が担う範囲は次である。

- JSON object 1 個であること
- 必須キーと型
- 閉じた enum
- 配列の件数上下限
- null を許す欄と許さない欄

prompt は schema が表すキー一覧、型、enum、件数上限を繰り返さず、候補の意味比較、field 間の意味関係、source pack 境界だけを伝える。repair prompt も validator error と、その違反を直すための意味規則に絞り、schema 全体を自然文で再掲しない。

コードの validator が担う範囲は次である。

- request ごとの source pack や WorkspaceContext、CapabilityChoiceView に存在する参照
- schema だけでは表せない、同じ参照の重複や主参照と補助参照の重複
- kind と stance、capability 実行可否のような相互制約
- 内部識別子禁止、改行禁止、正規化順のような意味規則

schema は request ごとの動的 enum を持たない。
`decision.kind` は `comparison_scope` に応じた閉じた許可集合とする。`foreground_selection` の factor ref は文字列の shape と件数上限だけを schema で求め、その request の `WorkspaceContext.workspace_candidates[].factor_ref` に存在すること、候補があるときの `primary_factor_ref` 必須、候補が無いときの空選択は validator で検証する。
比較 scope に存在しない結果種別の排他 payload は、必須キーを残したまま `null` 型へ固定する。たとえば `comparison_scope=outward_speech` の `capability_request / autonomous_run` は `null` だけを許可する。

閉じた object は `additionalProperties: false` とし、その object の全 property を `required` にする。
任意に見える欄もキーは常に出し、値を `null` または空配列にする。
開いた map は最終 `capability_request.input`、`capability_input_generation.input` と `qualifiers_hint` だけである。これらは `additionalProperties: true` の object とし、JSON 文字列へはしない。

能力実行では、`decision_generation` / `autonomous_step_generation` の choice schema と `capability_input_generation` の input schema を別 request にする。前段は canonical な `capability_id` と短い `target_ref` だけを返し、後段だけが選択済み 1 件の入力 schema を受ける。どちらかが失敗しても単段生成や別対象へ切り替えない。

`pattern`、`if` / `then`、ルートの巨大な `anyOf`、`$ref` は使わない。

## OpenRouter

`model` の provider が `openrouter` の structured 呼び出しでは、`extra_body.provider.require_parameters` を `true` にする。
既存の `reasoning` extra_body は上書きせず同居させる。

これは `response_format` を無視する endpoint へ落ちて自由文になることを防ぐためである。
`response_format` を支える endpoint が無いときは、その呼び出しを失敗とする。
`web_search_enabled` や `reasoning_effort` と同時に指定して、三つを同時に支える endpoint が無いときも、schema を外して再送しない。

OpenRouter 以外では `response_format` だけを渡し、`provider` は付けない。

## 失敗

次はいずれも明示失敗であり、自由文 completion や `json_object` へ切り替えない。

- model または endpoint が `json_schema` を受けない
- schema 自体が provider に拒否される
- 2 回目の出力も parse または validator を満たさない

provider が schema または非対応で拒否した初回は repair しない。
JSON として壊れている場合と、JSON は通るが validator が落ちる場合は、同じ `response_format` のまま repair prompt で 1 回だけ再生成する。
repair 回数と failure 範囲は [LLM補助契約共通.md](LLM補助契約共通.md) と各 role 文書を正とする。

## 観測

DEBUG には operation と schema `name` を残す。
schema 全文、raw prompt 全文、LLM 生レスポンス全文は標準保存しない。

## モデルプリセットとの関係

structured output の on/off を model_preset に持たない。
structured role では常に `json_schema` を付ける。
選択する model と接続先が structured output を支えることが、そのプリセットを使う前提である。
