# vision.capture 画像意味理解

## 目的

`vision.capture` と通常会話の添付画像は、raw image payload を永続化せず、LLM が生成した詳細な視覚説明へ変換して扱う。
詳細な視覚説明は、その場の判断だけでなく、後続会話、想起、記憶更新、日次整理の根拠として保存する。

この文書は、画像意味理解、視覚記録、派生要約、日次整理の意味境界を定める。
視覚 source の実行設計は [26_視覚機能実装設計.md](26_視覚機能実装設計.md) を正とする。
通常会話の添付画像の API 入力は [24_対話画像入力.md](24_対話画像入力.md) を正とする。
`world_state` への反映は [22_world_state.md](22_world_state.md) を正とする。

## 基本方針

視覚入力は `desktop / camera / attachment` の種類で保存可否を分けない。
source kind は入力元ラベルであり、保存・記憶化は観測内容、会話との結びつき、重要度、反復性で決める。

raw image、base64、OCR 全文、UI 座標、資格情報、内部 URL、配送先 client は保存しない。
一方で、LLM が画像から生成した `visual_summary_text` は、視覚経験の詳細説明として保存する。
短い要約は検索、一覧、重複判定、日次整理の派生値であり、詳細説明を置き換えない。

## 視覚記録

画像意味理解の正本レコードは `visual_observation_record` とする。
最小 shape は次とする。

```json
{
  "visual_observation_id": "visual_observation:...",
  "observed_at": "2026-04-27T23:00:00+09:00",
  "source_kind": "desktop",
  "source_label": "メイン画面",
  "vision_source_id": "vision_source:main_display",
  "trigger_kind": "capability_result",
  "image_input_kind": "vision_capture_result",
  "detailed_summary_text": "Slack の general チャンネルが前景で、左側にチャンネル一覧、中央に現在のスレッド、右側に補助ペインが見えている。背景に通知バッジがいくつかあり、会話確認中の画面に見える。",
  "confidence_hint": "medium",
  "scene_entities": ["Slack", "general チャンネル", "チャンネル一覧", "スレッド", "通知バッジ"],
  "activity_labels": ["連絡確認"],
  "environment_labels": [],
  "uncertainty_notes": [],
  "redaction_notes": [],
  "related_cycle_id": "cycle:...",
  "related_episode_id": null,
  "duplicate_group_id": null,
  "importance_score": 0.42,
  "retention_status": "active"
}
```

`detailed_summary_text` は LLM が返した詳細な視覚説明を保持する。
後から「観覧車が写っていたか」「どのファイルが開いていたか」のような視覚確認を行う場合は、この詳細説明を根拠にする。
不確実な対象は「らしき」「可能性がある」のように不確実性を本文と `uncertainty_notes` に残す。

`visual_observation_record` は raw image の代替ではない。
画像に写っていた内容を完全再現する責務は持たない。
ただし、後続会話の視覚確認に必要な主要物体、場所、背景要素、活動、状態は落とさない。

## 派生データ

視覚記録から次の派生データを作る。

| データ | 役割 |
|------|------|
| `visual_observation_index` | 検索、embedding、一覧表示、重複判定のための短い索引 |
| `scene_entities` | 後から照会しやすい物体、場所、画面要素、背景要素 |
| `activity_labels` | 何をしているかの短い活動ラベル |
| `environment_labels` | 場所、周囲、環境状態の短いラベル |
| `daily_visual_digest` | 1 日単位の視覚記録整理結果 |
| `memory_candidate` | 重要、反復、継続、会話結合を満たす長期記憶候補 |

派生データは `detailed_summary_text` を置き換えない。
検索に使う `short_summary_text` や `embedding_text` は、詳細説明へ辿る入口として扱う。

## LLM 契約

現行設計では専用 role を増やさず、`model_preset.roles.input_interpretation` を使う。

LLM に渡す source pack は少なくとも次を持つ。

```json
{
  "trigger_kind": "capability_result",
  "image_input_kind": "vision_capture_result",
  "time_context": "2026年4月27日 月曜日 23時00分（日本時間）",
  "client_context": {
    "vision_source_id": "vision_source:main_display",
    "source_kind": "desktop",
    "source_label": "メイン画面",
    "active_app": "Slack",
    "window_title": "general | Slack",
    "locale": "ja-JP"
  },
  "observation_summary": {
    "capability_id": "vision.capture",
    "vision_source_id": "vision_source:main_display",
    "source_kind": "desktop",
    "source_label": "メイン画面",
    "image_count": 1,
    "error": null
  },
  "current_input_summary": "vision.capture の非同期結果。前景アプリは Slack。ウィンドウタイトルは general | Slack。キャプチャ画像を 1 件受け取った。"
}
```

画像は source pack の JSON に埋め込まず、multimodal message の image part として別に添付する。
1 回の解釈で使う画像件数はコード側で制限する。
`vision.capture` 由来の source pack には `vision_source_id / source_kind / source_label` を含める。
通常会話の添付画像では `image_input_kind=conversation_attachment` を使う。

LLM の出力は JSON object 1 個に固定する。

```json
{
  "summary_text": "遊園地の屋外風景が写っている。近くにメリーゴーランドがあり、周囲に人や柵が見える。背景には観覧車らしき大きな円形構造物が見える。案内看板や園内設備も一部見えている。",
  "confidence_hint": "medium"
}
```

契約は次とする。

- トップレベルキーは `summary_text / confidence_hint` だけにする
- `summary_text` は詳細な視覚説明にする
- `summary_text` は改行なし、内部識別子なしにする
- 主要な物体、場所、背景要素、活動、状態、変化を落とさない
- 後から視覚確認に使えるよう、目立つ物体と背景要素を具体的に書く
- 不確実な対象は断定せず、「らしき」「可能性がある」として残す
- 細かな OCR 全文、座標、UI 構造、資格情報、内部 URL、配送先 client、base64 本文を書かない
- `confidence_hint` は `low / medium / high` のいずれかにする

## パイプライン統合

`vision.capture` の非同期 capability result と会話添付画像では、共有判断パイプラインへ入る前に画像意味理解を行う。

1. 画像入力を受ける
2. raw image を保存せず、LLM へ multimodal input として渡す
3. LLM が詳細な `summary_text` を返す
4. `observation_summary.image_interpreted=true` と `visual_summary_text` を付ける
5. `visual_summary_text` を `visual_observation_record.detailed_summary_text` として保存する
6. `scene_entities / activity_labels / environment_labels` と検索用 index を派生する
7. `VisualObservationContext` として `recall_hint / recall_pack / decision / reply` に渡す
8. `vision.capture` 由来の視覚観測は `world_state.visual_context` 更新候補にする
9. 会話や行動と結びつく場合は episode に紐づける
10. 重要、反復、継続、会話結合を満たす場合は記憶候補にする

この段階では、raw image から直接 `world_state` 行や memory row を作らない。
`visual_observation_record` と派生データを経由して、短期状態、episode、記憶候補へ進める。

## world_state との関係

`world_state.visual_context` は現在判断に近い短期状態である。
`visual_observation_record` は後から参照できる視覚記録である。
両者は同じではない。

`vision.capture` は `source_kind` に関係なく `world_state.visual_context` 更新候補になる。
ただし `world_state` へ入れる値は詳細説明そのものではなく、現在判断に効く短い状態要約にする。
詳細説明は `visual_observation_record` に残し、`world_state` から必要に応じて参照できるようにする。

通常会話の添付画像は、会話入力の補助視覚文脈として扱う。
添付画像だけから現在外界の `world_state.visual_context` は更新しない。
会話と結びついた視覚記録、episode、記憶候補は作成対象にする。

## 日次整理

視覚記録は 1 日 1 回の background 整理対象にする。
日次整理は詳細説明を一括で消す処理ではない。
詳細記録を選別、統合、索引化し、反復や重要な経験を記憶候補へ育てる処理である。

日次整理は次を行う。

1. 当日の `visual_observation_record` を集める
2. 類似する連続観測を `duplicate_group` にまとめる
3. 会話に出た記録、ユーザーが関心を示した記録、新規性の高い記録を保持対象にする
4. 低変化の連続作業画面は group summary に圧縮する
5. 反復した活動、環境、作業対象を `memory_candidate` にする
6. 重要な視覚体験を episode と長期記憶へ接続する
7. `daily_visual_digest` を作る

現行実装では、前日以前の未整理日を background worker が処理する。
日次整理は対象日の `visual_observation_record` を全件読み、毎分キャプチャのような高頻度入力でも同じ日の記録を整理対象から落とさない。
連続する類似視覚記録には `duplicate_group_id` を付ける。
詳細説明は残し、低変化 group の中間記録だけ `retention_status=compressed` にする。
日ごとの整理結果は `daily_visual_digest` として保存する。
`daily_visual_digest.memory_candidate_summaries` は記憶候補であり、background worker が 2 日以上の類似候補だけを制限付きで `memory_unit` へ昇格する。

保持対象は次である。

- 会話と結びついた画像
- 後から確認されそうな物体、場所、出来事を含む画像
- 新規性が高い画像
- ユーザーが明示的に関心を示した画像
- 判断や感情に影響した画像

圧縮対象は次である。

- 同じアプリ、同じ机、同じ部屋、同じ作業の連続観測
- 変化の少ない定期キャプチャ
- 低重要の重複観測

削除または検索除外対象は次である。

- 失敗キャプチャ
- 意味の薄い重複
- 秘密情報を含む可能性が高い説明
- ユーザーが削除または記憶禁止を明示した記録

## 検索優先度

`visual_observation_record.retention_status` は詳細説明の有無ではなく、検索と整理での優先度を表す。
`active` は通常検索対象である。
`compressed` は詳細説明を保持したまま、通常想起での優先度を下げる。
`excluded` はユーザー削除、記憶禁止、秘密情報の可能性がある場合だけ使い、通常想起と digest 昇格の対象から外す。

視覚記録検索は次の順で候補を並べる。

1. `retention_status=active`
2. query text と詳細説明の一致度
3. `importance_score`
4. `observed_at` の新しさ
5. `retention_status=compressed`

`compressed` は検索対象から消さない。
ユーザーが明示的に「そのときの画面を細かく確認したい」と求めた場合は、`compressed` も最大 3 件まで開く。
通常の「あったっけ」「最近どうだった」のような確認では、`active` と `daily_visual_digest` を優先する。

## しきい値調整

視覚整理のしきい値は固定仕様ではなく、実ログを根拠に調整する運用値である。
初期値は次とする。

- 連続観測の duplicate 判定類似度: `0.86`
- 1 日あたりの整理対象上限: `500`
- 1 回の worker 実行で処理する未整理日数: `7`
- `RecallPack.visual_observations` の投入上限: `3`
- `RecallPack.visual_daily_digests` の投入上限: `2`
- `daily_visual_digest` からの `memory_unit` 昇格上限: 1 日 `3`

調整では次を観測する。

- digest ごとの `record_count / group_count / compressed_count`
- `visual_observations` と `visual_daily_digests` が reply に使われた頻度
- `compressed` から後で参照された件数
- 昇格した `memory_unit` が訂正、矛盾、削除された件数
- 画像由来記憶がユーザーに過剰または不気味に見えた事例

しきい値は、記録を失わない方向ではなく、断定と昇格を抑える方向へまず調整する。

## 想起と回答

後続会話で視覚確認が必要な場合、通常の長期記憶だけでなく `visual_observation_record` を検索する。

検索順は次を基準にする。

1. 直近会話と episode
2. `visual_observation_index` の `scene_entities / embedding_text`
3. `visual_observation_record.detailed_summary_text`
4. `daily_visual_digest`
5. 長期 memory

現行実装では、検索一致した視覚記録と直近の視覚記録を `RecallPack.visual_observations` へ少数投入する。
応答と判断は `visual_observations[].detailed_summary_text` の範囲で、画像内の対象有無を確認する。

`daily_visual_digest` は、特定の画像内対象を直接断定する根拠ではなく、1 日単位の視覚経験の索引である。
後続会話で「昨日は何をしていた」「最近いつも何が見えていた」のような日単位、反復、傾向を確認する場合は、`daily_visual_digest` を `RecallPack.visual_daily_digests` へ投入する。
特定物体の有無確認では、まず `visual_observation_record.detailed_summary_text` を優先し、digest だけしか無い場合は「その日の整理上はそう見える」と不確実性を残す。

回答では不確実性を維持する。
例えば詳細説明に「背景に観覧車らしき大きな円形構造物が見える」とある場合、回答では「観覧車らしきものは写っていた」と述べ、断定しすぎない。

## 実装済みの順序

視覚記録の追加実装は次の順序で入った。

1. `daily_visual_digest` を想起へ投入する
2. `daily_visual_digest.memory_candidate_summaries` を長期記憶候補へ昇格する
3. `retention_status=compressed` の検索優先度を下げる
4. inspection/API で digest と整理結果を確認できるようにする
5. 実ログに基づいて類似度、件数、昇格しきい値を調整する

この順序にした理由は、想起で digest を使える状態にしてから、誤記憶化しやすい長期記憶昇格を制限付きで入れるためである。
検索優先度、inspection、しきい値調整は、想起と昇格の挙動を観測可能にしてから行う。

## failure の扱い

画像意味理解は、入力サイクルの中で明示的に扱う。

- validator 失敗時は 1 回だけ再生成する
- 再生成後も契約を満たさない場合は、そのサイクルを failure として残す
- silent fallback で画像なし入力へ丸めない
- 失敗理由は `observation_summary` と `cycle_trace` に残す

## inspection

inspection では少なくとも次を見られるようにする。

- `observation_summary.image_interpreted`
- `observation_summary.visual_summary_text`
- `observation_summary.visual_confidence_hint`
- `visual_observation_id`
- `retention_status`
- 日次整理後の `duplicate_group_id`

`cycle_trace.input_trace.observation_summary` にも同じ要約を残す。
ただし raw image payload 自体は inspection に出さない。

## 他設計との関係

- 視覚 source と capability 実行は [26_視覚機能実装設計.md](26_視覚機能実装設計.md) を正とする
- `world_state` への反映先は [22_world_state.md](22_world_state.md) を正とする
- capability wire は [api/05_実行連携.md](api/05_実行連携.md) を正とする
- repo 全体の LLM 判断原則は [20_LLM判断優先方針.md](20_LLM判断優先方針.md) を正とする
