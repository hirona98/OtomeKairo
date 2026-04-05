# MVP実装計画

<!-- Block: Role -->
## この文書の役割

この文書は、MVP をどの順で実装していくかを整理するための計画文書である。

ここで持つのは次だけに絞る。

- 今どこまで設計が閉じているか
- 次にどの順で実装するか
- どんな場合に docs を追加するか
- 後段へ送る論点は何か

ここでは、内部フローの厳密な手順、クラス設計、DB カラムまでは持たない。
それらはコードを正とし、必要になったときにだけ関連する設計 docs へ短く追記する。
外向き API の厳密な仕様は、この文書ではなく `design/14_API仕様.md` を正とする。

<!-- Block: Split -->
## docs の使い分け

OtomeKairo の docs は、次の 2 つに分ける。

- `design/`
  - 長く残る設計判断と契約を置く
- `plan/`
  - 実装順と直近の進め方だけを置く

この構成では、会話サイクルの厳密な流れや関数分割のような内部実装は、原則としてコードを正とする。

<!-- Block: DocBoundary -->
## docs に残すものとコードに寄せるもの

docs に残すのは、少なくとも次である。

- OtomeKairo と `CocoroConsole` の責務境界
- `reply / noop / future_act / internal_failure` の意味境界
- `events / episode_digests / memory_units / revisions` の保存責務
- 設定、ランタイム状態、モデル設定資源の意味
- 外向き API の厳密な仕様
- デバッグ記録で必ず残す情報

逆に、次はコードへ寄せる。

- 関数やクラスの分割
- モジュール間の厳密な呼び出し順
- 一時オブジェクトの細かな shape
- 実装中だけ使う補助メモ

<!-- Block: CurrentState -->
## 現在地

いまの OtomeKairo は、基本設計と機能設計がほぼ閉じた段階にある。

固定済みの主な論点は次である。

- 設定と `runtime_state` の境界
- 人格設定の粒度
- モデル役割とモデル設定資源
- 接続 bootstrap と認証
- デバッグ記録
- 自発判断の `future_act` 候補キュー
- 記憶設計の MVP 境界

したがって、次の本命は追加 docs を増やすことではなく、MVP のコードを縦切りで通すことである。

<!-- Block: Order -->
## MVP の実装順

MVP 実装は、次の順で進める。

1. 会話 1 サイクルの最小縦切りを通す
2. ターン単位の監査と最小保存を通す
3. 記憶更新を通す
4. 自発起床を通す
5. 接続、設定、運用面を通す

### 1. 会話 1 サイクルの最小縦切り

最初に通すのは次の流れである。

- 会話観測を受ける
- `RecallHint` を作る
- `RecallPack` を組む
- `reply / noop / internal_failure` を返す

この段階では、まず 1 サイクルを崩さず通すことを優先する。
内部オブジェクトの細部はコード側で素直に定義してよい。

### 2. ターン単位の監査と最小保存

次に、1 サイクルの結果を追えるようにする。

- `events`
- `retrieval_runs`
- `cycle_summary`
- 段階トレース

ここで重要なのは、「後から判断理由を追えること」であって、最初から保存系を過度に抽象化しないことである。

### 3. 記憶更新

その後で、会話 1 サイクルから記憶を育てる。

- `episode_digests`
- `memory_units`
- `revisions`
- `affect_state`

記憶更新は、すでに `design/memory/` に置いた責務境界を守って実装する。

<!-- Block: MemoryConcrete -->
## 記憶実装の具体化

記憶実装では、保存と想起の正本を早い段階で SQLite へ寄せる。
ここは後から差し替えると影響が大きいため、MVP でも最初から最終形に近い骨格で入れる。

この段階で固定する実装方針は次である。

- 記憶と監査の保存先は `SQLite` を正本にする
- 連想レーンの埋め込み検索は `sqlite-vec` を使う
- 文字列一致による検索は使わない
- `memory_set` は編集可能な設定資源として残しつつ、その内部データは SQLite の内部テーブルへ保持する
- 現在の `server_state.json` は、当面は設定資源の正本として残してよい
- ただし、`events`、`retrieval_runs`、`cycle_summary`、`cycle_trace` は SQLite 側へ移し、JSONL 追記は増やさない

### SQLite の責務境界

SQLite 側で正本として持つのは、少なくとも次である。

- `events`
- `episode_digests`
- `memory_units`
- `memory_links`
- `revisions`
- `affect_state`
- `retrieval_runs`
- `cycle_summaries`
- `cycle_traces`
- `vector_index_entries`

一方で、次は当面 `server_state.json` 側に残してよい。

- `selected_persona_id`
- `selected_memory_set_id`
- `memory_enabled`
- `wake_policy`
- `desktop_watch`
- `selected_model_preset_id`
- `persona`
- `memory_set`
- `model_preset`
- `model_profile`

この分離により、記憶本体は関係検索と更新履歴に向く保存系へ寄せつつ、設定面の既存 API は崩さずに進められる。

### SQLite 初期化方針

SQLite 接続では、少なくとも次を固定する。

- DB ファイルは専用の `memory.db` とする
- `foreign_keys = ON`
- `journal_mode = WAL`
- `busy_timeout` を設定する
- 起動時に `sqlite-vec` extension を load する

`sqlite-vec` の仮想テーブルは、少なくとも次の 2 系統を持つ。

- `memory_units` 用の埋め込み index
- `episode_digests` 用の埋め込み index

`vector_index_entries` は metadata を持つ通常テーブルとし、実ベクトル本体は `sqlite-vec` の仮想テーブルへ置く。
これにより、設計上の `vector_index_entries` 契約を保ちながら、距離検索は `sqlite-vec` に任せられる。

ここでの検索方針は次で固定する。

- 構造レーンは、正規化済みカラムへの条件指定だけで候補を取る
- 連想レーンは、`sqlite-vec` の近傍検索だけを使う
- `summary_text` や `raw_text_or_payload` への `LIKE`、部分一致、全文検索を標準経路に入れない
- 候補が足りなくても、文字列一致検索へフォールバックしない

### `memory_set` と内部テーブルの関係

`memory_set` は、引き続き設定面で編集できる軽量な資源として扱う。
ただし、その実体である記憶データは、すべて `memory_set_id` をキーに SQLite へ保持する。

この段階での基本ルールは次とする。

- `events` から `revisions` までの全レコードは `memory_set_id` を持つ
- 想起と更新は、常に現在の `selected_memory_set_id` だけを見る
- `delete_memory_set(memory_set_id)` では、その `memory_set_id` に属する内部テーブルの行も同一トランザクションで削除する
- `select_memory_set(memory_set_id)` は設定の切り替えだけを行い、内部記憶のコピーや移送はしない

### 実装スライス

記憶実装は、次の順で縦に通す。

#### 3-1. 保存層の差し替え

最初にやるのは、JSONL 監査を SQLite へ移すことである。

- `events`
- `retrieval_runs`
- `cycle_summaries`
- `cycle_traces`

この段階では、会話結果はまだ空の `RecallPack` でもよい。
重要なのは、以後の記憶更新が SQLite 上の `event_id` を根拠として使えるようにすることである。

#### 3-2. `turn consolidation` の保存骨格

次に、1 サイクルの保存後に最低限の記憶更新を行う。

- `episode_digests` を `1 判断サイクル = 1 digest` で作る
- `memory_units` 候補を LLM で抽出する
- 比較キーで既存候補を引く
- `create / reinforce / refine / supersede / revoke / dormant / noop` を決める
- 変化があったものだけ `revisions` を残す
- `affect_state` を更新する

ここで重要なのは、反射的に高度な再整理へ進まないことである。
まずは `turn consolidation` だけで `preference`、`commitment`、`fact`、`interpretation` を安定して回せるようにする。

現状の実装は、次まで入っている。

- `episode_digests` の保存
- `memory_interpretation` 契約の追加
- `preference`、`commitment`、`interpretation` の候補抽出
- 比較キーだけを使う `create / reinforce / refine / supersede` の反映
- `revisions` の保存
- `affect_state` の upsert

この段階では、まだ次は未着手である。

- `revoke / dormant / noop` の更新判定
- `fact` の安定抽出
- `sqlite-vec` を使う連想レーン

#### 3-3. 構造レーンの実装

次に、SQLite の通常問い合わせだけで組める想起を先に通す。

- `active_commitments`
- `relationship_model`
- `user_model`
- `self_model`
- `active_topics`
- `conflicts`

この段階では、`primary_intent` と `focus_scopes` を主ルーティングに使い、`status`、`commitment_state`、`scope`、`memory_type` を条件に候補を集める。
設計上、`commitment` と `relationship` は構造レーン優先なので、まずここを強くする。
ここで使う比較軸は、`scope_type`、`scope_key`、`memory_type`、`status`、`subject_ref`、`predicate`、`object_ref_or_value`、`commitment_state` のような半構造化項目に限定する。
自由文の文字列一致検索には寄せない。

現状の実装は、次まで入っている。

- `RecallBuilder` を新設し、空の `RecallPack` を廃止
- `active_commitments` を `memory_type=commitment` と active な `commitment_state` で別枠取得
- `user_model`、`relationship_model`、`self_model` を `scope_type / scope_key / status` で取得
- `active_topics` を `scope_type=topic` と `episode_digests.has_open_loops` で補助取得
- `episodic_evidence` を `episode_digests` の構造条件だけで取得
- `conflicts` を比較キー単位の併存候補から抽出
- `retrieval_runs` と `cycle_traces` に `selected_episode_digest_ids` と `recall_pack_summary` を保存

この段階では、まだ次は未着手である。

- `event_evidence` の限定ロード

#### 3-4. 連想レーンの実装

構造レーンの後で、`sqlite-vec` による連想レーンを足す。

- `memory_units.summary_text` の埋め込み
- `episode_digests.summary_text`
- `episode_digests.outcome_text`
- `episode_digests.open_loops`

検索結果は補助候補としてだけ使い、次を守る。

- `vector-only` 候補を断定根拠にしない
- rerank と section boost に使う
- `event_evidence` が必要なときだけ、関連 `event_id` を最大 1-3 件開く

現状の実装は、次まで入っている。

- `vector_index_entries` metadata table と `memory_unit_vec` / `episode_digest_vec` を追加
- `turn consolidation` 後に、新規または更新された `memory_units` / `episode_digests` だけを差分 upsert
- query 側は `observation_text` を埋め込み、`memory_units` と `episode_digests` を近傍検索
- 連想候補は `user_model` / `relationship_model` / `self_model` / `active_topics` / `episodic_evidence` に補助的に混ぜる
- `vector-only` 採用は `cycle_traces.recall_trace` に分かる形で残す

この段階では、まだ次は未着手である。

- `mentioned_entities` / `mentioned_topics` を強く使う query 強化
- `event_evidence` の限定ロード
- 非同期の埋め込み更新ジョブ

#### 3-5. `RecallPack` の本接続

想起結果を、設計どおりの役割別 `RecallPack` として判断と返答へ渡す。

- `self_model`
- `user_model`
- `relationship_model`
- `active_topics`
- `active_commitments`
- `episodic_evidence`
- `conflicts`

この段階で、空配列固定の最小 slice をやめる。
`decision_generation` と `reply_generation` は、少なくとも `RecallPack` の要約を見て判断と返答を組み立てるようにする。

現状の実装は、次まで入っている。

- `decision_generation` に `recent_turns` と `internal_context` を渡す
- `internal_context` は `TimeContext`、`AffectContext`、`RecallPack` の 3 つで組む
- `AffectContext` は `affect_state` を `scope` 条件だけで読んで `surface / background` に圧縮する
- `RecallPack` は prompt 直前に section ごとの短い内部要約へ圧縮して渡す
- `decision_generation` prompt は `conflicts` を確認寄り判断の根拠として扱う
- `reply_generation` prompt は `RecallPack` の要約を見て、継続文脈や確認質問を組み立てる
- mock 実装でも `active_commitments`、`episodic_evidence`、`conflicts`、`AffectContext` が返答に効く
- `cycle_traces.decision_trace` に internal context summary を残す

この段階では、まだ次は未着手である。

- `event_evidence` の限定ロード
- `RecallPack` を使った `future_act` 判断分岐

#### 3-6. `reflective consolidation` の入口

最後に、非同期または明示起動で動かせる再整理の入口を置く。

初期段階でやるのは次まででよい。

- `summary` 系 `memory_units` の生成
- `inferred -> confirmed` の見直し
- 低重要トピックの `dormant` 化
- `relationship` と `self` の長期変化の再評価

ここでは、広い高次推論へ進まない。
`design/memory/06_想起と判断ユースケース.md` で「現状では難しい」としている領域は後段へ送る。

現状の実装は、次まで入っている。

- `reflection_runs` を SQLite に保存する
- post-turn の best-effort trigger 判定を入れる
- trigger は `chat 8 ターン`、`前回から 24 時間`、`高 salience digest の偏り`、`supersede / revoke`、`relationship` 変化の最小版を使う
- `memory_type=summary` の `memory_units` を `self / user / relationship / topic` 単位で生成または更新する
- `summary` は初期状態では保守的に `inferred` で作り、十分な digest 数と複数ターン根拠があるときだけ `confirmed` にする
- `inferred -> confirmed` は scope 単位ではなく compare 単位かつ複数ターン根拠で引き上げる
- 古く低重要な `topic` 系を `dormant` 化する
- 反射更新でも `revisions` を残し、変化した `memory_units` の vector index を更新する
- 失敗時は `reflection_runs.failure_reason` と `cycle_traces.memory_trace` に残し、監査イベントも追記する

この段階では、まだ次は未着手である。

- `relationship` と `self` の高次な長期変化分析
- `events` の限定ロードを使う精密根拠確認
- 完全な非同期ジョブ化

### モジュール分割の目安

コード分割はコード側を正とするが、少なくとも責務は次で分ける。

- SQLite 接続と migration
- 監査保存
- `turn consolidation`
- 構造レーン想起
- `sqlite-vec` 連想レーン
- `RecallPack` 組み立て
- `reflective consolidation`

今の `service.py` に直接すべて詰め込まず、保存層と記憶処理は早めに外へ出す前提で進める。

### 先にやらないこと

記憶を最初から本格実装する一方で、次はこの段階では広げない。

- 正確引用の保証
- 長い時系列の完全再構成
- 複合意図の同時最適化
- 曖昧参照の全面解決
- 多段の関係推論
- 複雑な外界行動の実行計画

ここを一緒に背負うと、記憶基盤の実装速度が落ちる。
最初に固定すべきなのは、あくまで記憶の保存責務、更新責務、想起責務の骨格である。

### 4. 自発起床

会話サイクルと保存系が通った後で、自発起床を足す。

- `wake_policy`
- `future_act` 候補キュー
- `reply / noop / future_act` 判断
- 過剰反応抑制

ここでは、会話サイクルの共通部を流用しつつ、起床固有の分だけ足す。

### 5. 接続、設定、運用面

最後に、実運用に必要な面を整える。

- bootstrap
- 状態取得
- 設定取得と設定変更
- 列挙面

この順にすることで、先に中核サイクルを成立させてから運用面を固められる。

<!-- Block: AddDocsRule -->
## docs を追加する条件

実装中に docs を追加してよいのは、次のような場合だけである。

- 外部契約を新しく固定するとき
- 外向き API 仕様を追加または変更するとき
- 永続化の意味や責務を変更するとき
- 設定やランタイム状態の意味を変更するとき
- デバッグ記録の保証内容を変更するとき

逆に、次のために新しい docs は作らない。

- 実装順の細かなメモ
- 関数分割の説明
- クラス設計の説明
- その時点のコードをなぞるだけの内部フロー説明

API 仕様を変更した場合は、同じ変更内で `design/14_API仕様.md` を更新する。

<!-- Block: Deferred -->
## 後段へ送る論点

MVP 実装を止めない前提で、次は後段へ送る。

- 将来の身体性と外界接続
- 複雑な外界行動の実行設計
- 能力追加時の人格、記憶、行動の再編
- 人間互換へ寄せるための長期拡張

これらは重要だが、いまの MVP 実装の前提にはしない。

<!-- Block: Gate -->
## 実装を進めてよい前提

少なくとも次が満たされていれば、MVP 実装へ進んでよい。

- 外部契約と永続責務の境界が docs 全体で一致している
- `reply / noop / future_act / internal_failure` の意味が docs 全体で一致している
- `future_act` 候補キューと長期記憶の境界が docs 全体で一致している
- デバッグ記録で何を残すかを説明できる
- `CocoroConsole` は編集 UI であり、設定の正本ではないと docs 全体で一致している
