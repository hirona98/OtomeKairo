# vision.capture 画像意味理解

## 目的

`desktop_watch` が取得した `vision.capture` の image payload を、raw payload のままではなく、共有判断パイプラインへ渡せる短い観測要約へ変換する。

この段階の目的は、画像から万能な構造理解を作ることではない。
`client_context` だけでは落ちる「今の画面で何が前景か」を、判断、`world_state`、inspection に反映できるようにすることに絞る。

## 境界

この段階で行うこと:

- `desktop_watch` の image payload を `model_preset.roles.input_interpretation` で要約する
- 要約結果を `input_text` と `observation_summary` に反映する
- 要約結果を `world_state` 更新の入力へ渡す
- inspection で `image_interpreted=true` と観測要約を見られるようにする

この段階で行わないこと:

- raw image payload の永続化
- OCR の全文抽出
- UI 要素や座標の構造化
- 通常会話 API での画像入力
- capability 実行一般への横展開

## 入力と出力

`vision.capture` の client -> server wire 形式自体は変えない。
`images` は Data URI 配列のまま受け取る。

この段階では、画像意味理解の出力として次だけを持つ。

| 項目 | 役割 |
|------|------|
| `summary_text` | 画面の前景を 1 文で表した短い要約 |
| `confidence_hint` | `low / medium / high` の確からしさ |

`summary_text` は、判断に効く前景だけを 1 文に圧縮する。
対象は「どのアプリ / 画面 / 会話 / 作業が前景か」「何が起きていそうか」の第一段に留める。

## LLM 契約

現行設計では専用 role を増やさず、`model_preset.roles.input_interpretation` を使う。

LLM に渡す source pack は少なくとも次を持つ。

```json
{
  "trigger_kind": "desktop_watch",
  "time_context": "2026年4月27日 月曜日 23時00分（日本時間）",
  "client_context": {
    "active_app": "Slack",
    "window_title": "general | Slack",
    "locale": "ja-JP"
  },
  "observation_summary": {
    "capability_id": "vision.capture",
    "image_count": 1,
    "error": null
  },
  "current_input_summary": "desktop_watch 観測。前景アプリは Slack。ウィンドウタイトルは general | Slack。キャプチャ画像を 1 件受け取った。"
}
```

画像は source pack の JSON に埋め込まず、multimodal message の image part として別に添付する。
1 回の解釈で使う画像件数はコード側で制限する。

LLM の出力は JSON object 1 個に固定する。

```json
{
  "summary_text": "Slack の general チャンネルが前景で、会話一覧と現在のスレッドが見えている。",
  "confidence_hint": "medium"
}
```

契約は次とする。

- トップレベルキーは `summary_text / confidence_hint` だけにする
- `summary_text` は 1 文、改行なし、内部識別子なしにする
- `summary_text` は見えている内容の短い要約に留め、推測を膨らませない
- `confidence_hint` は `low / medium / high` のいずれかにする
- raw payload、資格情報、内部 URL、配送先 client、base64 本文を出力しない

## パイプライン統合

`desktop_watch` では、共有判断パイプラインへ入る前に画像意味理解を行う。

1. `vision.capture` の response を受ける
2. image payload を LLM で短い `summary_text` へ変換する
3. `observation_summary.image_interpreted=true` と `visual_summary_text` を付ける
4. `input_text` に観測要約を足して、以後の `recall_hint / recall_pack / decision / reply` に渡す
5. `world_state` 更新 source pack に `visual_summary_text` を渡す

この段階では、`summary_text` を唯一の意味出力として扱う。
画像から直接 `world_state` 行や capability result row を作らない。

## failure の扱い

画像意味理解は、`desktop_watch` サイクルの中で明示的に扱う。

- validator 失敗時は 1 回だけ再生成する
- 再生成後も契約を満たさない場合は、その `desktop_watch` サイクルを failure として残す
- silent fallback で `client_context` 主体へ戻さない
- 失敗理由は `observation_summary` と `cycle_trace` に残す

## inspection

inspection では少なくとも次を見られるようにする。

- `observation_summary.image_interpreted`
- `observation_summary.visual_summary_text`
- `observation_summary.visual_confidence_hint`

`cycle_trace.input_trace.observation_summary` にも同じ要約を残す。
ただし raw image payload 自体は inspection に出さない。

## 他設計との関係

- `world_state` への反映先は [22_world_state.md](22_world_state.md) を正とする
- capability wire は [api/05_実行連携.md](api/05_実行連携.md) を正とする
- repo 全体の LLM 判断原則は [20_LLM判断優先方針.md](20_LLM判断優先方針.md) を正とする
