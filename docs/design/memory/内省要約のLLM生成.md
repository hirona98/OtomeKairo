# 内省要約の LLM 生成

## 位置づけ

この文書は `reflective consolidation` における `summary_text` 生成を定める。
LLM 補助処理の共通境界、source pack、出力、failure、inspection の共通ルールは [../llm/LLM補助契約共通.md](../llm/LLM補助契約共通.md) を正とする。

## 採用方針

OtomeKairo では、`reflective consolidation` 全体を LLM 任せにはしない。

- trigger 判定はロジックで行う
- evidence の収集と絞り込みはロジックで行う
- `summary` を作る下限判定はロジックで行う
- `confirmed` / `dormant` の状態遷移はロジックで行う
- `create / refine / reinforce / supersede` の action 解決はロジックで行う
- `summary_text` の生成と、候補本文の対象・話者・根拠の照合を、それぞれ専用の LLM 呼び出しへ任せる

要するに、`何を保持するか` は構造化で管理し、`どう言語化するか` だけを LLM に任せる。

## 目的

- `summary_text` を定型文ではなく、証拠に沿った自然な要約へする
- `self / relationship / entity / topic` の違いを無理な手書き分岐ではなく、文面で柔らかく表現できるようにする
- `predicate` や `qualifiers` が増えても、文面生成コードを増やし続けなくて済むようにする
- 既存の `memory_units` 契約と `reflective consolidation` の安定性を壊さない

## 対象範囲

この設計の対象は次だけである。

- `reflective consolidation` で生成する `memory_type=summary` の `summary_text`
- そのための evidence pack 構築
- LLM 入出力契約
- 失敗時の扱いと観測

## 対象外

この設計では次は変えない。

- `reflective consolidation` の trigger 条件
- `summary` を出す最小 evidence 条件
- `confirmed` / `dormant` の判定規則
- `events` の広い再読
- `memory_action` の解決規則
- `RecallPack` の section 構成

## 基本構成

内省の集約要約は、`subject_ref` で対象を示し、`predicate=long_term_pattern` と `summary_text` で継続理解を表す。独立した目的語を持たないため `object_ref_or_value=null` とする。scope の整理キーを目的語の参照として生成しない。一般の候補メモの目的語・値は [記憶更新と再整理.md](記憶更新と再整理.md) の契約に従う。

`reflective consolidation` の summary 生成は、次の 5 段に分ける。

1. ロジックで、今回文面を作り直す dirty scope を決める
2. ロジックで dirty かつ eligible な scope ごとの evidence pack を作る
3. LLM に dirty scope 群の `summary_text` を 1 回の structured output で生成させる
4. 別の LLM 呼び出しで、候補本文そのものを同じ一次根拠へ照合する
5. 照合が成立した文面だけを使って、既存の action resolver で `summary` 系 action を決める

ここで重要なのは、LLM の出力をそのまま state 遷移へ使わないことである。
未変更の active memory scope を毎回再生成しない。

同じ scope の既存要約に対して `correct / revoke / supersede` が入った場合は、新規要約を作る根拠件数の下限とは分けて、訂正後の根拠から既存要約を再生成する。更新の前提が変わった既存要約を、直近の根拠が少ないという理由で以前の主張のまま残さない。訂正後の現在理解と過去の経緯は、要約生成の LLM が根拠へ照合する。

要約生成は `memory_units` の状態、有効期間、確認時刻を現在の理解に対応させ、`episodes` をその時期の経験として扱う。既存の集約要約は保存先の解決にだけ使い、再生成の入力には含めない。現在の記憶と新しい経験に基づいて組み直し、生成済み要約の誤帰属や断定を再び根拠として採用する循環を作らない。以前の正しい理解から後の理解へ変わった経緯は時間的変化として表し、過去の経験と現在の記憶を同時点の主張として並べない。当時から誤りだった説明の訂正とは区別する。

要約対象の scope、個々の主張の対象、元発言の話者は分けて保つ。
記憶の `subject_ref / scope_type / scope_key / qualifiers`、episode の primary scope と出典の参加者を入力へ残し、選択済み項目が参照する元イベントに照合する。
本人の説明、第三者への言及、現在の個の発言・解釈の区別は、既存の要約生成 LLM が構造化した対象・話者と本文から判断する。
`entity` の要約は `scope_key` が示す対象について裏付けられる理解に絞る。別の `subject_ref` の記憶は対象との対応を確認する補助として扱う。
`relationship` の要約では、現在の個の方針、感情、行動は「こちら」のものとして相手と区別する。人格、気分、感情は、本人の発言の裏付けとして扱わない。
元イベントの参照が空、または話者や本文の記録がない場合は、その情報の限界を保って記憶の対象・種別・出典に沿った理解を記述する。

## 論理 role

この処理の論理 role は `memory_reflection_summary` とし、選択中の `model_preset` を使う。

この role の責務は次だけである。

- dirty な scope 群の evidence pack を読み、各 scope の `summary_text` 候補を返す

この論理 role を `memory_interpretation` と分ける理由は次である。

- turn ごとの記憶解釈と、長期要約では入出力契約が異なる
- turn ごとの記憶解釈と独立したプロンプト、出力契約、失敗境界を持てる
- prompt 改修が turn consolidation 側へ波及しにくい

保存前の照合 role は `memory_reflection_summary_review` とする。生成時と同じ model preset と persona snapshot を使い、候補本文と一次根拠だけを新しい呼び出しへ渡す。生成時の自己説明や正当化は渡さない。候補は変更せず、照合結果だけを返す。

すべての論理 role は選択中の同じ `model_preset` を使う。

## `memory_postprocess_job` への追加情報

`memory_postprocess_job` は embedding 同期だけでなく、会話時点の設定 snapshot も保持する。
`summary_text` を LLM 生成するようにした後は、postprocess job に次も含める。

- `selected_persona_id`
- `personas.{selected_persona_id}` の snapshot
- `selected_model_preset_id`
- 選択中 `model_preset` の snapshot

background worker は、会話時点の設定 snapshot を使って reflective summary を生成する。
実行時の current 設定を引き直してはいけない。

## dirty scope

文面生成の対象は、次の和集合に入る scope だけである。判定は構造値だけで行い、自然文比較はしない。

| 根拠 | 取る scope |
| --- | --- |
| この reflection 窓の `episodes` | `primary_scope_type` / `primary_scope_key` |
| この turn の `memory_actions`（訂正 action を含む） | 対象 `memory_unit` の `scope_type` / `scope_key` |
| この reflection 内で persist した `affect_state` の create / update | その `target_scope_type` / `target_scope_key` |
| 直前の reflection run で summary 生成に失敗した scope | `failed_scopes` |
| eligible だが既存 `summary` が無い scope | 初回 backfill |

次は dirty にしない。

- ただ存在する active memory の scope
- turn consolidation の mood 更新そのもの。mood はほぼ毎 turn 変わるので、毎回 `self` を dirty にしない。`self` は episode、memory_action、`self_change` trigger、失敗再試行、未生成 backfill のときだけ対象にする

eligible 判定（件数下限）は今どおりコードが行う。dirty でも下限未満なら呼ばない。

1 回の LLM 呼び出しに載せる dirty scope は最大 6 件である。それを超える場合は同じ契約で分割する。

## evidence pack の設計

LLM に渡す入力は、dirty scope ごとの圧縮済み evidence pack と、選択した項目の一次根拠の共有表からなる source pack とする。
各 pack は request-local な `scope_ref` を持つ。突合は `scope_ref` だけを使う。

最低限、次を含める。

```json
{
  "scopes": [
    {
      "scope_ref": "scope:0",
      "scope_type": "relationship",
      "scope_key": "self|person:external-123",
      "scope_label": "self|person:external-123 の関係文脈",
      "summary_status_candidate": "inferred",
      "dominant_memory_types": ["relation", "interpretation"],
      "support_kinds": ["episodes", "memory_units", "persona_context", "affect_state"],
      "evidence_counts": {
        "episodes": 3,
        "memory_units": 4,
        "support_cycles": 3,
        "open_loops": 1
      },
      "persona_context": {
        "persona_prompt_excerpt": "必要以上に媚びず、相手を一人の相手としてまっすぐ扱う。"
      },
      "affect_state": [
        {
          "affect_label": "気がかり",
          "intensity": 0.63,
          "confidence": 0.74,
          "summary_text": "相手の負担を気にかける感情が続いている。"
        }
      ],
      "episodes": [
        {
          "episode_type": "conversation",
          "primary_scope_type": "relationship",
          "primary_scope_key": "self|person:external-123",
          "source_interaction_refs": ["interaction:example"],
          "source_participant_refs": ["person:external-123"],
          "source_event_refs": ["source_event:0"],
          "formed_time_label": "2026年4月12日 10時30分（日本時間）",
          "summary_text": "前回の相談の続きと体調確認をした。",
          "outcome_text": "結論は保留で、次回も様子を見ることになった。",
          "open_loops": ["体調の変化をまた確認する"],
          "salience": 0.82
        }
      ],
      "memory_units": [
        {
          "memory_type": "relation",
          "scope_type": "relationship",
          "scope_key": "self|person:external-123",
          "subject_ref": "self|person:external-123",
          "qualifiers": {"source_participant_refs": ["person:external-123"]},
          "source_event_refs": ["source_event:0"],
          "predicate": "talk_again",
          "object_ref_or_value": "topic:health",
          "summary_text": "また体調の話の続きをしたい流れがある。",
          "status": "inferred",
          "formed_time_label": "2026年4月12日 10時30分",
          "last_confirmed_time_label": "2026年4月12日 10時30分",
          "valid_from_time_label": null,
          "valid_to_time_label": null,
          "confidence": 0.72,
          "salience": 0.61
        }
      ]
    }
  ],
  "source_events": {
    "source_event:0": {
      "kind": "speech",
      "role": "assistant",
      "speaker_ref": "self",
      "participant_refs": ["person:external-123"],
      "interaction_ref": "interaction:example",
      "formed_time_label": "2026年4月12日 10時30分00秒",
      "text": "また体調の話の続きを聞かせてほしい。"
    }
  }
}
```

入力の原則は次である。

- `episodes` は新しい順で最大 6 件
- `memory_units` は重要度順で最大 8 件
- 各 item は全文ではなく、`summary_text` と構造化項目の要点だけに絞る
- 記憶 item は `subject_ref / scope_type / scope_key / qualifiers` を保つ。条件、時期、頻度、極性、出典の情報を、scope の対象だけから補わない
- episode item は `episode_type / primary_scope_type / primary_scope_key / source_interaction_refs / source_participant_refs` を保つ。参加者は発言者や主張の対象とは分けて扱う
- 各 item の `source_event_refs` は、同じバッチの共有表 `source_events` を参照する。同じ event は複数項目・複数 scope の根拠でも 1 回だけ渡す
- `event_id` や `cycle_id` は LLM へ渡さない
- `formed_at` のような正本 timestamp は、生活文脈向けに整形した `formed_time_label` として渡す
- 記憶 item は `formed_time_label / last_confirmed_time_label / valid_from_time_label / valid_to_time_label` を持つ。不明な時刻は `null` とし、有効期間を形成時刻から補わない
- `relationship / self` では、selected persona から作る `persona_context` の短い summary を補助入力として渡す
- `self` では `mood_state`、`relationship / entity` では対応する `affect_state` を補助入力として渡す
- `support_kinds` は、その scope を再構成したときに実際に使った補助層だけを列挙する

一次根拠の読み出しは、選択した最大 6 episode・最大 8 memory item が直接参照する event の集合に限る。
周辺会話や別 scope の event を検索で補充せず、既存要約の根拠をさらに遡って展開しない。
元イベントは `kind / role / speaker_ref / participant_refs / interaction_ref / source_kind / terminal_status` と、記録されている発言本文・実行目的や結果の要点、生活文脈の発生時刻だけを渡す。
本文は文字数で切らず、元の意味単位を保つ。raw DB row、内部判断、外部サービス応答 payload は渡さない。
参照された event を取得できない場合は、その scope の `load_source_events` 失敗として記録し、文面生成・保存を行わない。他の scope は継続する。
参照が最初から空の記憶に、話者や元発言を生成して付け足さない。
通常は最大 6 scope のバッチにつき生成 1 回と独立照合 1 回を呼ぶ。生成契約を満たす候補が 0 件の場合は照合を呼ばない。参照読み出しで失敗した scope、生成に失敗した scope は照合入力から外す。
保存する summary action の根拠 event は、実際に選択した入力項目の参照の和集合とする。従来の独立した先頭 12 件への切り詰めは行わない。
関連 memory unit の参照も、要約入力に選択した項目へ揃える。根拠件数と要約状態の候補値は、従来どおり scope 全体から求める。

## LLM 出力契約

LLM の出力は JSON object 1 個に固定する。

```json
{
  "summaries": [
    {
      "scope_ref": "scope:0",
      "summary_text": "最近のやり取りでは、体調を気にかけながら無理のない進め方を探る流れが続いている。"
    }
  ]
}
```

トップレベルキーは `summaries` だけである。
各要素は `scope_ref` と `summary_text` だけを持つ。
`scope_ref` は source pack にある request-local ref だけを使う。
各バッチで渡された `scope_ref` ごとに、対応する項目をちょうど 1 件ずつ返す。

`summary_text` の契約は次とする。

- 必須
- 文字列
- 前後空白を除いて空でない
- 簡潔にまとめる
- 140 文字以内
- 改行を含まない
- `event_id` や `memory_unit_id` のような内部識別子を含まない
- 逐語引用を主目的にしない
- 単発出来事ではなく、反復して見えている傾向として書く

呼び出し側は、返却された全項目をそのバッチの `scope_ref` と突合してから採用する。
同じ `scope_ref` が複数ある場合は、文面の一致や返却順によらず、その scope を契約違反として扱う。
欠けた dirty scope と項目契約違反も、該当 scope の失敗として記録する。
この場合、該当 scope の summary action を作らず、既存要約を維持する。

未知の `scope_ref`、文字列でない ref、ref を持たない項目は、対応する scope を推測せず、応答の契約違反として別に記録する。
別バッチの `scope_ref` も、その応答では未知の ref として扱う。
既知の ref が一意で項目契約も正しい scope は、他項目の失敗があっても採用する。
envelope 自体の契約違反や生成失敗は、そのバッチの全 scope の失敗として扱い、他バッチは続ける。
LLM client の validator は envelope（`summaries` 配列）だけを検証する。
各項目の検証、重複 ref、未知 ref の扱いは呼び出し側が行う。

episode 窓の起点は、最後に正常に評価した reflection run（`updated` または `no_change`）の `finished_at` である。
`failed` では進めない。失敗 scope は `previous_failure` で再試行する。
重複・欠落・未知 ref を理由とする追加の即時 LLM 呼び出しは行わず、失敗 scope は次の trigger で評価する。

## 保存前の独立照合

生成契約を通った候補について、同じ項目と一次根拠の共有表へ `summary_candidates`（`scope_ref / summary_text`）を加え、`memory_reflection_summary_review` へ渡す。
返却形式は次とする。

```json
{
  "reviews": [
    {
      "scope_ref": "scope:0",
      "reason_summary": "本文の状態と方針が、それぞれ元発言の話者と対象に対応している。",
      "supported": true,
      "scope_supported": true
    }
  ]
}
```

トップレベルは `reviews` だけ、各項目は `scope_ref / reason_summary / supported / scope_supported` だけを持つ。
`reason_summary` は空でない文字列、`supported / scope_supported` は boolean とする。
候補の全 ref にちょうど 1 件ずつ返し、重複・欠落・未知 ref は生成応答と同じ参照検証で扱う。

照合は候補本文の各主張の対象・話者・根拠を読み、本人の説明、第三者への言及、現在の個の発言や解釈を区別する。
本文自体で主体が曖昧な場合、現在の個の方針・感情を別人へ移す場合、人物を特定できない観測を対象人物へ結びつける場合は `supported=false` とする。
本文は `scope_key` の対象に帰属する記憶として保存される。対象名を省いた一般的な観測要約でも、特定人物についての理解として裏付けられなければ `supported=false` とする。
推測や時点も元の不確実性と対応させる。整理先の `scope_key / subject_ref` だけを人物同定の根拠にしない。
`supported` は本文の主張の裏付け、`scope_supported` は本文を scope の対象へ帰属させる裏付けを独立に判定する。一般的な観測が事実でも、人物を同定できなければ `scope_supported=false` とする。
自然文の意味は照合 LLM が判断し、コードは構造、参照の一意性、両判定が `true` であることを検証する。

照合の否認、項目不正、envelope 不正、provider 失敗は `failure_stage=verify_summary_attribution` で記録し、該当候補を保存しない。
他の正常な候補は保存し、失敗 scope は既存の `previous_failure` による次の trigger で評価する。
本文の書き換えや、否認時の生成し直しは行わない。照合理由を memory unit の本文や人格に保存しない。

## プロンプト方針

system prompt では、少なくとも次を明示する。

- `reflective consolidation` の summary 文面だけを書く
- 新しい事実を足さない
- 渡された evidence pack の外を推測で埋めない
- `summary_status_candidate=inferred` なら断定しすぎない
- `summary_status_candidate=confirmed` でも過剰に強い人格断定にしない
- 人格の適用は [../configuration/人格設定詳細.md](../configuration/人格設定詳細.md) を正とし、要約の根拠は `episodes / memory_units` に従う
- 主張の対象と元発言の話者を、`subject_ref / qualifiers / source_event_refs / source_events` に沿って照合し、本人の説明、第三者についての説明、現在の個の発言・解釈の帰属を保つ
- `mood_state` と `affect_state` は、`episodes / memory_units` と整合する範囲だけで補助的に使う
- `open_loops` は長期傾向に効くときだけ触れる
- 単発イベントの説明ではなく、継続パターンとして要約する

user prompt では、dirty scope 群の `scope_ref`、counts、persona_context / mood / affect の補助情報、episode 要点、memory_unit 要点、一次根拠の共有表を構造化して渡す。

## 処理フロー

実装時の flow は次とする。

1. 既存ロジックで trigger 判定をする
2. 既存ロジックで `episodes` と active `memory_units` を収集する
3. ロジックで dirty scope を決め、eligible 判定を通す
4. dirty かつ eligible な scope について evidence pack を構築し、`scope_ref` を付ける
5. `memory_reflection_summary` role で最大 6 scope ずつ LLM を呼び、`summaries` を得る
6. `scope_ref` で突合し、生成項目契約を検証し、独立照合で `supported / scope_supported` が両方 `true` の候補だけ、返ってきた `summary_text` を入れた candidate を `MemoryActionResolver` に渡す
7. 既存どおり `create / refine / reinforce / supersede` を解決する
8. 他の reflective 処理である `confirmed` 見直しと `dormant` 化を続ける
9. 結果を `reflection_runs` と `memory_trace` へ記録する

## 失敗時の扱い

この設計では fallback 文面生成を入れない。
LLM が失敗したときに、古いロジック生成へ戻すことはしない。

代わりに、失敗は次のように閉じ込める。

- ある scope の `summary_text` 生成だけが失敗したら、その scope の `summary` action だけを作らない
- 他 scope の summary 生成、`confirmed` 見直し、`dormant` 化は継続する
- reflective run 全体は、他の action が成立していれば `updated`、何も変化がなければ `no_change` とする
- store 書き込みや worker 自体の異常のように、run 全体が完了できない場合だけ `failed` とする

## 観測と監査

`reflection_runs.payload_json` には、少なくとも次を追加する。

- `summary_generation.requested_scope_count`
- `summary_generation.succeeded_scope_count`
- `summary_generation.failed_scopes`
- `summary_generation.response_errors`
- `summary_generation.dirty_scope_count`
- `summary_generation.dirty_reasons`
- `summary_generation.llm_call_count`

`dirty_reasons` の各要素は `scope_type`、`scope_key`、`reasons` を持つ。
`reasons` は `episode` / `memory_action` / `affect_update` / `previous_failure` / `missing_summary` / `self_change` の配列である。

`failed_scopes` の各要素は次を持つ。

- `scope_type`
- `scope_key`
- `failure_stage`
- `failure_reason`

`failed_scopes` には、要求した実在 scope の失敗を各 1 件記録する。
`response_errors` は、対応先を特定できない返却項目ごとに `batch_scope_refs`、`item_index`（0 始まり）、`failure_stage`、`failure_reason` を持つ。
要求側の ref 群と項目位置、違反理由だけを残し、対応不明の返却本文は記録しない。
この記録自体を架空の scope の失敗や再試行対象には数えない。要求した ref の欠落は、別途 `failed_scopes` に残す。

inspection で現在地を追いやすくするため、`memory_trace.reflective_consolidation` にも summary generation の件数要約を持たせる。
あわせて `memory_trace.drive_state_update.scope_supports` に、scope、support 種別、summary 更新有無を残す。

監査 event は `reflective_summary_generation_failure` とし、scope 単位の失敗を追えるようにする。
対応先を特定できない項目は `reflective_summary_response_contract_failure` として、`response_errors` と同じ項目を残す。
evidence pack、`summary_status`、`salience`、`confidence`、`MemoryActionResolver` は state 遷移と保存のために維持し、文面生成だけを専用の LLM 生成へ分離する。

## この設計で守ること

- `summary_text` は自然文にするが、記憶の正本はあくまで構造化項目である
- LLM は状態遷移を決めない
- 根拠の少なさを、文面の巧さでごまかさない
- 失敗しても deterministic な他処理まで巻き込んで止めない
