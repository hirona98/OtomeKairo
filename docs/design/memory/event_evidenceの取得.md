# `event_evidence` の取得

## 位置づけ

この文書は、`RecallPack.event_evidence` の取得、項目契約、件数上限と、想起・共有資料に使うeventの欠損参照修復、失敗境界、観測を定める。
想起の意味境界と現在の理解・過去の報告の扱いは [想起と判断.md](想起と判断.md) を正とする。

`event_evidence` は、選定した出来事の本文・判断理由と出所を、その回の判断へ渡す一時的な根拠である。
コードが event の選定と記録項目の投影を行い、後続の LLM が人格と現在の文脈に基づいて意味を判断する。

## 取得フローと上限

1. 想起で選んだ `memory_units / episodes` を基に、event 根拠の取得が必要か判定する。
2. section 優先順と各候補の根拠参照から、標準の `event_id` を最大8件選ぶ。
3. 曖昧さや履歴確認が必要なら、同じ選定済み候補に残る sibling event を最大8件追加する。
4. `load_and_repair_event_evidence()` で標準・precise の event をそれぞれ DB から取得し、欠損参照を修復する。
5. 各 record の項目の型を確認し、取得順ではなく選定順で `RecallPack.event_evidence` に積む。
6. 後続の判断・本文生成へ、同じ本文と出所、対象ごとの根拠欠損を渡す。

この取得処理では LLM を呼ばず、本文や判断理由は記録された文字列を保持する。
改行と前後空白も保ち、文字数による切断や再要約は行わない。
件数の上限は標準8件と追加8件の合計16件とし、長文の本文もこの件数境界の中で扱う。
後続モデルの入力上限などのエラーは、その LLM 呼び出しの失敗として扱う。

### 標準選定

選定済み候補に根拠 event があり、`primary_recall_focus` が `commitment / fact / relationship / episodic`、または `time_reference=past` のときに取得する。
section 優先順の各候補から根拠 event をラウンドロビンで取り、IDの重複を除く。
候補内の優先順は既存どおりインデックス `1, 0, 2`、残りの参照順とする。

### precise 追加

追加の確認理由は次のいずれかである。

- `risk_flags` に `ambiguous_reference / mixed_intent / time_ambiguous / weak_memory_cue` がある。
- `primary_recall_focus` が `commitment / relationship / fact` で、`time_reference` が `past / persistent` である。
- 標準選定が1件以下で、その選定元に追加の根拠 event がある。

追加対象は標準選定元に残る sibling event に限り、標準選定との重複を除いて最大8件とする。
最終的な並びは標準選定、precise追加の順とする。
`selected_event_ids` は標準選定を表し、追加分は trace の `precise_selected_event_ids` に記録する。

## 項目契約

内部の各項目は `event_id / kind` と、元 record にある次の項目を持つ。

| 項目 | 意味と型 |
| --- | --- |
| `kind` | 記録された event 種別。空でない文字列 |
| `role` | 元 event の話者種別。文字列または `null` |
| `speaker_ref / interaction_ref` | 記録された話者・会話参照。文字列または `null` |
| `participant_refs` | 記録された参加者参照の配列または `null` |
| `created_at` | 記録された発生日時。文字列または `null` |
| `text` | 本文全体。文字列または `null` |
| `reason_code / reason_summary` | 記録済みの判断理由。文字列または `null` |
| `result_kind / external_result_kind` | 記録済みの結果種別。文字列または `null` |
| `pending_intent_summary` | `intent_kind / intent_summary / reason_summary` の説明用投影。それぞれ文字列または `null`。object全体も `null` を許す |

記録にない任意項目は追加せず、記録された `null` は保持する。
本文のない decision は、記録済みの判断理由や結果種別をそれぞれの項目として渡す。
人物・会話参照は記録された出所として扱う。

通常のモデル入力では `event_id` を外し、表の項目だけを投影する。
`cycle_id / memory_set_id` やその他のDB管理項目はこの投影に含めない。
日時のモデル向け表示は既存の共通整形に従う。本文と判断理由の文字列はそのまま渡す。
正確な原文・日時・出典を扱う `evidence_pack` の参照契約は [想起と判断.md](想起と判断.md#現在の理解と過去の報告) を参照する。

## 失敗境界

- 選定した event が見つからなければ、その ID を `load_event` の失敗として記録する。
- 識別できた record の項目が型契約を満たさなければ、その event を `validate_event` の失敗として記録する。
- 失敗した event を除き、取得・検証できた event を選定順で後続へ渡す。
- 全件失敗でも、選定 ID と各失敗を trace・audit に残し、その失敗だけでは cycle を終了しない。
- DB接続・クエリなど、store 呼び出し自体の例外は呼び出し元へ返す。
- JSONの破損、識別子や参照配列のカラムとJSON本文の不一致、参照修復・監査保存の失敗は呼び出し元へ返す。

event の欠落を、別の event、記憶要約、固定文で補わない。
取得対象がない場合は空配列と件数0を返す。

## 欠損参照の自動修復

通常のevent根拠、選定記憶の直接の更新元報告、共有条件の確認資料には、同じ取得・修復処理を使う。
内部結果は `events / missing_event_ids / repaired_sources` とする。起点の `cycle_id` と検出日時を渡す。
読み取り専用の `load_events_for_evidence()` は、訂正や約束の履行更新など、別の根拠要件を持つ処理で引き続き使う。

取得するIDを重複除去して件数上限を適用し、その対象だけについて、選択中の記憶集合の `events` に存在するかを確認する。
取得件数の上限や話者の種類によって返さなかったeventを、欠損扱いにしない。
不存在を確認したIDだけを、同じ記憶集合の現行 `memory_units.evidence_event_ids / episodes.linked_event_ids / entity_registry.evidence_event_ids` から削除する。
その回に選定しなかったレコードも、同じ欠損IDを参照していれば修復する。
専用JSONカラムと `payload_json` の参照を同時に更新する。

存在確認、参照の削除、`event_reference_repaired` 監査eventの保存は同じトランザクションで確定する。
監査には起点cycle、欠損ID、修復対象の種類・ID、対象ごとの削除ID、修復日時を残す。ログはIDと件数だけとし、記憶本文や元発話を出さない。
すでに記録済みで参照も修復済みの欠損に対して、修復監査を重複保存しない。
途中のエラーでは削除と監査を一緒にロールバックする。

記憶本文、残っている根拠、人物、status、確信度、記憶の日時は保持する。改訂の過去スナップショットや既存の監査は書き換えない。
対象には [データモデルの参照修復情報](データモデル.md#event参照の修復情報) を保存する。
同じ回の取得済み候補にも修復結果を反映し、後続が削除前の参照を再取得しないようにする。
通常の記憶更新、古い更新候補の保存、記憶集合の複製でも修復情報を保持し、保存境界で削除済み参照の再登録を防ぐ。
参照配列の修復は本文や検索用意味テキストを変えないため、埋め込みの再生成は行わない。

根拠が一部または全部欠けても、その回の判断・応答を続ける。
判断モデルへは、取得できた元資料と対象ごとの欠損情報を一緒に渡し、記憶の要約と原文確認の範囲を区別する。
共有許可を確認できない内容は開示を留保し、確認できる根拠で回答や他の行動を判断する。
この修復自体ではLLMを追加で呼ばない。

## trace と audit

`RecallPack`、`recall_trace`、retrieval run の取得記録は `event_evidence_loading` とする。
`recall_trace` の取得記録は次を持つ。

- `requested_event_count`: 標準と追加を合わせた要求件数
- `loaded_event_count`: 取得できた record の件数
- `succeeded_event_count`: 項目検証を通り、根拠に採用した件数
- `failed_items`: `event_id / kind / failure_stage / failure_reason` の配列
- `precise_evidence_used / precise_reason_codes / precise_reason_summary`
- `precise_selected_event_ids / precise_requested_event_count / precise_loaded_event_count`

retrieval run には件数・precise情報の要点を保存し、失敗件数は `failed_count` に記録する。
audit の `event_evidence_precise_check` は追加選定の判定を表す。
各失敗は `event_evidence_load_failure` として、`source_event_id / source_event_kind / failure_stage / failure_reason` を保存する。

記憶の直接の更新元を読む `memory_link_context.source_reports` は独立した取得経路である。
取得範囲は [想起と判断.md](想起と判断.md#memory_links-の想起利用) を参照する。取得時の欠損参照修復は本書の共通規則に従い、欠損IDと修復結果を `source_report_loading` に残す。
