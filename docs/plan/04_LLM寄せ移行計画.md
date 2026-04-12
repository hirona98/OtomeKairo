# LLM寄せ移行計画

## この文書の役割

この文書は、OtomeKairo の現在実装を見直し、意味判断をできる限り LLM へ寄せるための移行計画を管理する。

ここでは次を扱う。

- 現在ロジック主体になっている箇所の棚卸し
- LLM へ寄せる対象と、コード側へ残す境界
- 段階的な移行順
- 各段階で追加する role / contract / docs の見通し

repo 全体の原則は [20_LLM判断優先方針.md](../design/20_LLM判断優先方針.md) を正とする。

## 今回の前提

いったん post-MVP の個別拡張は保留にする。

- `relationship / self` の要約品質改善は、個別改善ではなく LLM寄せ移行の第一段として扱う
- `events` の限定ロードによる精密根拠確認は、LLM寄せ後の recall 再整理の中で扱う
- `desktop_watch` の image 意味理解は、LLM寄せ移行の後段で扱う

つまり、次フェーズは「新機能追加」ではなく「既存意味判断の LLM 化」である。

## 今回確認した変更対象

現行コードを確認した結果、次は意味判断をロジックで強く持っており、移行対象と判断した。

### 1. `reflective consolidation` の summary 文面

対象ファイル:

- `src/otomekairo/memory_reflection.py`
- `src/otomekairo/memory.py`

現状:

- `summary_text` の生成は `memory_reflection_summary` role へ移行済み
- evidence pack、`summary_status`、`salience`、`confidence`、action 解決はロジックに残している

移行方針:

- evidence pack はロジックで作る
- `summary_text` だけを LLM 生成に置き換える

詳細設計:

- [07_内省要約のLLM生成.md](../design/memory/07_内省要約のLLM生成.md)

### 2. `event_evidence` の圧縮表現

対象ファイル:

- `src/otomekairo/recall_event_evidence.py`

現状:

- `anchor / topic / decision_or_result / tone_or_note` を手書きロジックで作っている
- `event_id` の選択順も固定ロジックであり、意味的な圧縮ではなく整形寄りになっている

移行方針:

- `event_id` の上限管理や source 選定はロジックに残す
- 選ばれた event 群をどう短い根拠表現にするかは LLM へ寄せる

### 3. `RecallPack` の意味的選別と section 配置

対象ファイル:

- `src/otomekairo/recall.py`

現状:

- association query 文字列を固定ロジックで作っている
- query weight、association score、section limit 前の候補マージを固定ロジックで決めている
- `conflicts.summary_text` も固定文である

移行方針:

- candidate retrieval、件数上限、global limit はロジックに残す
- 候補群の意味的優先付け、section 配置、`conflicts` の説明文は LLM へ寄せる

### 4. `wake` の保留候補再選択

対象ファイル:

- `src/otomekairo/service_spontaneous.py`

現状:

- `_select_due_pending_intent_candidate()` は eligible 候補の中から最古の 1 件を選ぶだけである
- 「今どの候補を再介入対象にするべきか」という意味判断が入っていない

移行方針:

- due 判定、expiry、cooldown はロジックに残す
- eligible 候補群の中から今扱うべき候補を選ぶ判断は LLM へ寄せる

### 5. `wake` / `desktop_watch` の観測文組み立て

対象ファイル:

- `src/otomekairo/service_spontaneous.py`

現状:

- `client_context` を固定テンプレートで observation text にしている
- `desktop_watch` は画像意味理解を持たず、`client_context` 主体である

移行方針:

- capture protocol、client 接続、timeout はロジックに残す
- `client_context` と image をどう観測意味へ変換するかは LLM へ寄せる

## コード側へ残す境界

次は移行対象ではなく、引き続きコード側へ残す。

- `MemoryActionResolver` の状態遷移
- `wake_policy` / `desktop_watch` の validator
- capture request / response 契約
- background worker、queue、retry、timeout
- persistence と inspection 記録
- global limit、件数上限、dedupe、cooldown、expiry のような deterministic 制約

要するに、意味判断は LLM、状態境界はコード、という分離を崩さない。

## 移行順

次はこの順で進める。

1. `reflective consolidation` の summary 文面
2. `event_evidence` の LLM 圧縮
3. `RecallPack` の意味的 rerank / section 配置 / `conflicts` 文面
4. `wake` の pending-intent 候補選択
5. `desktop_watch` の観測意味理解

この順にする理由は次である。

- 1 と 2 は文面生成寄りで、state 境界を壊しにくい
- 3 は recall 品質への影響が大きいが、候補 retrieval と limit をロジックに残せる
- 4 は自発再介入の質に直結するが、先に recall 側を LLM寄せしてからのほうが一貫する
- 5 は image を含み、role / payload / client 契約の設計負荷が最も高い

## 各段階の実装計画

### Phase 1: `reflective consolidation` の summary 文面を LLM 化する

現状: 完了

対象:

- `src/otomekairo/memory_reflection.py`
- `src/otomekairo/memory.py`
- `src/otomekairo/llm.py`
- `src/otomekairo/llm_prompts.py`
- `src/otomekairo/llm_contracts.py`
- `src/otomekairo/llm_mock.py`
- `src/otomekairo/service_config.py`
- `src/otomekairo/service_common.py`
- `src/otomekairo/service.py`
- `src/otomekairo/defaults.py`

やること:

- `memory_reflection_summary` role を追加する
- evidence pack builder を実装する
- `summary_text` の structured output 契約を追加する
- postprocess job に role snapshot を含める
- `reflection_runs` と `memory_trace` に summary generation の件数要約を足す

完了条件:

- `_reflective_summary_text()` 系の本命依存がなくなる
- summary 文面の生成失敗が scope 単位で閉じ込められる

### Phase 2: `event_evidence` を LLM 圧縮へ移行する

対象:

- `src/otomekairo/recall_event_evidence.py`
- `src/otomekairo/recall.py`
- `src/otomekairo/llm.py`
- `src/otomekairo/llm_prompts.py`
- `src/otomekairo/llm_contracts.py`
- `src/otomekairo/llm_mock.py`

詳細設計:

- [08_event_evidenceのLLM圧縮.md](../design/memory/08_event_evidenceのLLM圧縮.md)

やること:

- selected `event_id` 群から短い evidence pack を作る
- `anchor / topic / decision_or_result / tone_or_note` を LLM に生成させる
- `event_evidence` の新契約を作る
- failure 時は silent に `event_evidence=[]` へ潰さず、event 単位 failure として inspection に残す

完了条件:

- `_event_evidence_anchor()` などの固定文組み立てに本命依存しない
- `RecallPack.event_evidence` が自然文寄りになる

### Phase 3: `RecallPack` の意味的選別を LLM 化する

対象:

- `src/otomekairo/recall.py`
- `src/otomekairo/llm.py`
- `src/otomekairo/llm_prompts.py`
- `src/otomekairo/llm_contracts.py`
- `src/otomekairo/llm_mock.py`

やること:

- retrieval で集めた候補群を LLM に渡し、section 優先度と採用候補を返させる
- `association_query_weight()` や固定 boost を段階的に削る
- `conflicts.summary_text` を LLM 生成にする
- global limit と dedupe はコード側で最後に強制する

完了条件:

- `RecallPack` の section 選別が固定 weight 依存ではなくなる
- `conflicts` が固定文ではなく、競合の意味に沿った説明になる

### Phase 4: `wake` の pending-intent 候補選択を LLM 化する

対象:

- `src/otomekairo/service_spontaneous.py`
- `src/otomekairo/llm.py`
- `src/otomekairo/llm_prompts.py`
- `src/otomekairo/llm_contracts.py`
- `src/otomekairo/llm_mock.py`

やること:

- eligible な pending-intent candidate 群を LLM へ渡す
- `selected_candidate_id | none` を structured output で返させる
- oldest-first の固定選択をやめる

完了条件:

- 再介入対象の選択が、意味的な優先度に基づく
- expiry / cooldown / due は従来どおり deterministic に守られる

### Phase 5: `desktop_watch` の観測意味理解を LLM 化する

対象:

- `src/otomekairo/service_spontaneous.py`
- `src/otomekairo/service.py`
- `src/otomekairo/llm.py`
- `src/otomekairo/llm_prompts.py`
- `src/otomekairo/llm_contracts.py`
- `src/otomekairo/llm_mock.py`
- 必要なら `docs/design/api/02_event_stream.md`

やること:

- `client_context + images` を受ける観測解釈 role を追加する
- `desktop_watch` 用 observation summary を LLM に生成させる
- 現在の固定 observation text を補助用途へ下げる

完了条件:

- `desktop_watch` が `client_context` 主体ではなく、画像の意味を判断へ使える

## 段階ごとの docs 更新方針

各 phase では、実装前にその処理専用の設計書を作る。

現時点で確定しているもの:

- Phase 1: [07_内省要約のLLM生成.md](../design/memory/07_内省要約のLLM生成.md)
- Phase 2: [08_event_evidenceのLLM圧縮.md](../design/memory/08_event_evidenceのLLM圧縮.md)

今後追加する想定:

- `RecallPack` の LLM 選別設計
- `wake` 候補選択の LLM 化設計
- `desktop_watch` 観測意味理解の設計

## 今は移行しないもの

次は、当面移行対象にしない。

- `MemoryActionResolver` の比較と状態遷移
- strict な JSON validator 群
- scheduler と runtime state
- `submit_vision_capture_response()` の protocol
- store schema と inspection の基本構造

## 着手順の結論

次にやるべき実装は Phase 1 である。

理由は次のとおりである。

- 既に専用設計書がある
- 影響範囲が `reflective consolidation` の summary 文面に閉じている
- それでいて「定型的な保持内容になりやすい」という現在の違和感に最も直接効く
