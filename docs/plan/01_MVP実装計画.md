# MVP実装計画

<!-- Block: Role -->
## この文書の役割

この文書は、OtomeKairo の MVP 実装をどう進めるかを管理するための計画文書である。

ここで持つのは次に限る。

- 現在地
- 完了済みの実装範囲
- 直近の実装順
- 後段へ送る論点

長く残る契約や責務境界は `docs/design/` を正とする。
関数分割、クラス分割、局所的な helper はコードを正とする。

<!-- Block: Principles -->
## 運用方針

この計画書は、次の前提で更新する。

- 設計判断は `docs/design/` を正とする
- 実装状態はコードを正とする
- この文書は「今どこまで通ったか」と「次に何をやるか」だけを持つ
- 仕様変更や実装変更をしたら、同じ変更内で docs も更新する
- 旧仕様互換、移行レイヤー、文字列一致検索のような方針外の実装は入れない

<!-- Block: Snapshot -->
## 現在地

現在の実装は、次の状態にある。

- 会話 1 サイクルの通常系は通っている
- bootstrap、設定取得、設定変更、catalog、inspection API は通っている
- 監査保存は `SQLite` 正本へ移っている
- 記憶基盤は `SQLite + sqlite-vec` 前提で通っている
- `turn consolidation`、構造レーン、連想レーン、`RecallPack` 接続、`reflective consolidation` の入口まで入っている

一方で、MVP 全体としてはまだ未完である。

- `future_act` はまだ内部結果に入っていない
- `wake_policy` は設定としてはあるが、実際の起床ループは未実装である
- `desktop_watch` も設定としてはあるが、観測源としては未実装である
- 連想レーンの非同期化は未完である
- 反射再整理の高次分析は未完である

要するに、今は「会話MVP + 記憶基盤」は通っており、「自発判断と運用ループ」が残っている段階である。

<!-- Block: FixedDecisions -->
## いま固定している前提

ここは、以後の実装で揺らさない。

- 記憶と監査の正本は `memory.db` に置く
- 埋め込み検索は `sqlite-vec` を使う
- 構造レーンは半構造化カラム条件だけで引く
- 連想レーンはベクトル近傍検索だけで引く
- 文字列一致検索、`LIKE`、全文検索へのフォールバックは入れない
- `server_state.json` は当面、設定資源の正本として残す
- 通常 API の外向き結果は MVP では `reply / noop / internal_failure` に固定する
- `future_act` は内部結果および内部候補キューとして扱い、通常 API の外向き結果には出さない
- `event_evidence` は必要時だけ最大 1-3 件の `events` を短い slot 型へ圧縮して作る

<!-- Block: CurrentCode -->
## 現在のコード構成

現在のコードは、少なくとも次の責務で分割している。

- `state_store.py`
  - `server_state.json` の read / write
- `store.py`
  - `FileStore` の facade
  - SQLite 側の query / migration / vector 永続化
- `service.py`
  - API と会話パイプラインの orchestrator
- `recall.py`
  - 構造レーンと連想レーンを含む `RecallPack` 組み立て
- `memory.py`
  - `turn consolidation` の orchestration
- `memory_actions.py`
  - `create / reinforce / refine / supersede / revoke / dormant / noop` の解決
- `memory_vector.py`
  - `sqlite-vec` 用の source text 構築と index 同期
- `memory_reflection.py`
  - `reflective consolidation`
- `llm.py`
  - `RecallHint`、`decision_generation`、`reply_generation`、`memory_interpretation`、embedding 呼び出し

<!-- Block: CompletedPhase1 -->
## 完了済み: 会話サイクルと基本API

この段階は完了済みである。

- bootstrap API
  - `probe_bootstrap`
  - `read_server_identity`
  - `register_first_console`
  - `reissue_console_access_token`
- 通常 API
  - `get_status`
  - `get_config`
  - `get_catalog`
  - editor state
  - persona / memory_set / model_preset の read / replace / delete
  - current selection patch
- 会話サイクル
  - `RecallHint`
  - `RecallPack`
  - `decision_generation`
  - `reply_generation`
  - `internal_failure` への畳み込み
- inspection
  - `list_cycle_summaries`
  - `get_cycle_trace`

<!-- Block: CompletedPhase2 -->
## 完了済み: 監査保存の SQLite 化

この段階も完了済みである。

`memory.db` には少なくとも次のテーブルがある。

- `events`
- `retrieval_runs`
- `cycle_summaries`
- `cycle_traces`
- `episode_digests`
- `memory_units`
- `revisions`
- `affect_state`
- `vector_index_entries`
- `reflection_runs`

この段階で実現できていることは次である。

- 1 サイクルの結果を SQLite へ保存する
- cycle 単位の inspection を SQLite から読む
- `selected_memory_set_id` にぶら下がる内部記憶を一括削除できる
- 旧 JSONL 依存を増やさない

補足として、`memory_links` は現行コードではまだ独立テーブルとして持っていない。
現在は `revisions.related_memory_unit_ids` で最小の関連履歴を持っている。

<!-- Block: CompletedPhase3 -->
## 完了済み: 記憶基盤の第一段

記憶基盤として、次までは入っている。

- `turn consolidation`
  - `episode_digests` 作成
  - `memory_interpretation` 契約
  - `memory_units` 更新
  - `revisions` 保存
  - `affect_state` 更新
- 更新判定
  - `create / reinforce / refine / supersede / revoke / dormant / noop`
  - compare key 単位の解決
  - 明示否定は `revoke + create`
  - 明示訂正の `fact` は `supersede + create`
  - 弱い `interpretation / relation` は `noop`
- 構造レーン
  - `active_commitments`
  - `self_model`
  - `user_model`
  - `relationship_model`
  - `active_topics`
  - `episodic_evidence`
  - `conflicts`
- 連想レーン
  - `memory_units` と `episode_digests` の埋め込み index
  - query embedding による近傍検索
  - `observation / entity / topic` の複数 query を重み付きで束ねる
  - `primary_intent / time_reference` による query 重み調整
  - `retrieval_lane=association` の補助採用
- `event_evidence`
  - 選択済み digest / memory に紐づく `event_id` を最大 3 件だけ読む
  - `anchor / topic / decision_or_result / tone_or_note` に圧縮する
- `RecallPack` 本接続
  - `decision_generation` と `reply_generation` へ internal context として渡す
  - trace に summary を残す
- `reflective consolidation`
  - `summary` 生成
  - `inferred -> confirmed` 見直し
  - 低重要 topic の `dormant` 化
  - vector 再同期
  - 失敗監査

ここまでで、「後から差し替えると大工事になる記憶の骨格」は通っている。

<!-- Block: InProgress -->
## 進行中: 記憶品質と内部判断の仕上げ

次の本命はここである。

### A. `future_act` の内部結果化

未着手なのは次である。

- `decision_generation` の内部結果を `reply / noop / future_act` へ広げる
- 通常 API の外向き結果は引き続き `reply / noop / internal_failure` に保つ
- trace に `future_act` 候補を残す

### B. `future_act` 候補キュー

未着手なのは次である。

- runtime-only の `future_act` 候補キューを持つ
- 起床判断時に再評価する
- memory 切り替えや再起動でクリアしてよい境界を守る

### C. 反射再整理の精度改善

未着手なのは次である。

- `relationship` と `self` の高次な長期変化分析
- `events` の限定ロードを使う精密根拠確認
- 重い再整理の完全非同期化

<!-- Block: PendingWake -->
## 未着手: 自発起床ループ

`wake_policy` と `desktop_watch` は設定としては存在するが、実際の起床ループはまだない。

ここでやることは次である。

- `wake_policy` に基づく判断機会の生成
- `desktop_watch` を観測源として接続する
- 起床時に `reply / noop / future_act` を内部判断する
- 連続起床時の過剰反応を判断側で抑える

この段階では、外界行動の実行まではやらない。
まずは「自発的に話すか、保留するか」の内部判断だけを通す。

<!-- Block: PendingOps -->
## 未着手: 運用硬化

MVP の体験を崩さずに使える状態へ寄せるため、後段で次をやる。

- embedding 更新の非同期ジョブ化
- `reflective consolidation` の非同期ジョブ化
- migration の整理
- 長時間 smoke の整備
- 仕様が固まった後の回帰テスト整備

現時点では、仕様変更がまだ続いているので、新規テストの大量追加は後ろに置く。

<!-- Block: NextOrder -->
## 直近の実装順

次はこの順で進める。

1. `future_act` を内部結果として導入
2. runtime-only の `future_act` 候補キューを導入
3. `wake_policy` と起床判断ループを通す
4. `desktop_watch` を観測源として繋ぐ
5. 反射再整理の高次分析を絞って足す
6. 非同期ジョブ化と運用硬化へ進む

この順にする理由は、先に記憶の使い道を仕上げ、その後で自発起床へ広げるほうが安全だからである。

<!-- Block: NotNow -->
## 先にやらないこと

MVP の間は、次を同時には背負わない。

- 正確引用の保証
- 長い時系列の完全再構成
- 曖昧参照の全面解決
- 複合意図の同時最適化
- 高価な多段関係推論
- 外界行動の直接実行
- 能力追加を前提にした大きな再編

ここを一緒に背負うと、MVP の芯である会話、記憶、自発判断の実装速度が落ちる。

<!-- Block: DoneDefinition -->
## MVP 完了条件

この計画書上では、少なくとも次を満たしたら MVP が一段落したとみなしてよい。

- bootstrap と通常 API の基本面が通っている
- 会話 1 サイクルの `reply / noop / internal_failure` が安定している
- 記憶基盤が `SQLite + sqlite-vec` 前提で通っている
- `future_act` が内部結果として動き、候補キューも扱える
- `wake_policy` に基づく自発起床が通っている
- trace を見れば判断と記憶更新の根拠を追える

<!-- Block: DocRule -->
## docs 更新ルール

この文書の更新ルールは次で固定する。

- 実装順や現在地が変わったら、この文書を更新する
- 外部契約や意味境界が変わったら `docs/design/` も同じ変更内で更新する
- plan 文書にコードの細かな内部手順を増やしすぎない
- コードをなぞるだけの文は増やさず、進行判断に必要な情報だけを残す
