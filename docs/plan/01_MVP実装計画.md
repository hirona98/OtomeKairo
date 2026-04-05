# MVP実装計画

## この文書の役割

この文書は、OtomeKairo の MVP 実装について「今どこまで通っていて、次に何をやるか」を管理するための計画書である。

ここでは次だけを扱う。

- 現在地
- 完了済みの実装範囲
- 既知の未完了項目
- 直近の実装順

長く残る契約、責務境界、データモデル、判断原則は `docs/design/` を正とする。
この文書は実装進捗の追跡を目的とし、設計の正本にはしない。

## 更新ルール

この計画書は、次の前提で更新する。

- 設計判断は `docs/design/` を正とする
- 実装状態はコードを正とする
- 実装や仕様を変えたら、同じ変更内で docs も更新する
- 古い予定ではなく、現時点の実装済み / 未着手 / 次アクションを優先して書く
- 方針外の実装は入れない

方針外として、少なくとも次は入れない。

- 文字列一致検索
- `LIKE`
- 全文検索へのフォールバック
- 旧仕様互換のためだけの移行レイヤー

## 現在地

現在の実装は、次の状態にある。

- 会話 1 サイクルの通常系は通っている
- bootstrap、設定取得、設定変更、catalog、inspection API は通っている
- 監査と記憶の正本は `SQLite` に寄っている
- 記憶基盤は `SQLite + sqlite-vec` 前提で通っている
- `turn consolidation`、構造レーン、連想レーン、`event_evidence`、`RecallPack` 接続、`reflective consolidation` の入口まで入っている
- `RecallHint` は validator 強化と 1 回再試行まで入っている
- `secondary_intents` は rerank、section boost、返答方針補助に効く
- 第三者や固有名は `focus_scopes` ではなく `mentioned_entities` で扱う
- `future_act` は内部結果として扱え、trace に候補要約を残せる
- runtime-only の `future_act` 候補キューがあり、dedupe / update / expiry metadata を持てる

一方で、MVP 全体としてはまだ未完である。

- `wake_policy` は設定としてはあるが、実際の起床ループは未実装である
- `desktop_watch` も設定としてはあるが、観測源としては未実装である
- embedding 更新と `reflective consolidation` の完全非同期化は未完である
- `relationship` / `self` の高次な長期変化分析は未完である
- 回帰テストはまだ本格整備していない

要するに、今は「会話MVP + 記憶基盤」は通っており、「自発判断と運用硬化」が残っている段階である。

## 固定している前提

ここは、以後の実装でも揺らさない。

- 記憶と監査の正本は `memory.db` に置く
- 埋め込み検索は `sqlite-vec` を使う
- 構造レーンは半構造化カラム条件だけで引く
- 連想レーンはベクトル近傍検索だけで引く
- 文字列一致検索、`LIKE`、全文検索へのフォールバックは入れない
- `server_state.json` は当面、設定資源の正本として残す
- 通常 API の外向き結果は MVP では `reply / noop / internal_failure` に固定する
- `future_act` は内部結果と内部候補キューに閉じ込め、通常 API の外には出さない
- `event_evidence` は必要時だけ最大 1-3 件の `events` を短い slot 型に圧縮して作る
- 第三者や固有名は `focus_scopes` に広げず、`mentioned_entities` で扱う

## 現在のコード構成

現在の主要責務は、少なくとも次のように分かれている。

- `src/otomekairo/state_store.py`
  - `server_state.json` の read / write
- `src/otomekairo/store.py`
  - `FileStore` facade
  - SQLite migration
  - query
  - vector 永続化
- `src/otomekairo/service.py`
  - API と会話パイプラインの orchestrator
- `src/otomekairo/recall.py`
  - 構造レーン
  - 連想レーン
  - `event_evidence`
  - `RecallPack` 組み立て
- `src/otomekairo/memory.py`
  - `turn consolidation` orchestration
- `src/otomekairo/memory_actions.py`
  - `create / reinforce / refine / supersede / revoke / dormant / noop` の解決
- `src/otomekairo/memory_vector.py`
  - `sqlite-vec` 用 source text 構築
  - index 同期
- `src/otomekairo/memory_reflection.py`
  - `reflective consolidation`
- `src/otomekairo/llm.py`
  - `RecallHint`
  - `decision_generation`
  - `reply_generation`
  - `memory_interpretation`
  - embedding 呼び出し

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
- memory / reflection の失敗を監査へ残せる
- 旧 JSONL 依存を増やさない

補足として、`memory_links` は現行コードでは独立テーブルとしてまだ持っていない。
現在は `revisions.related_memory_unit_ids` で最小の関連履歴を持っている。

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
  - `mentioned_entities / mentioned_topics` の query 強化
  - `secondary_intents` による rerank / section boost
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

## 完了済み: `RecallHint` 契約と記憶品質の補強

この段階で、最近の品質補強も反映済みである。

- `RecallHint` validator
  - 必須キー確認
  - enum 確認
  - `secondary_intents` の重複禁止、最大 2 件
  - `focus_scopes` / `mentioned_entities` / `mentioned_topics` の最大 4 件
  - `confidence` の `0.0-1.0` 確認
  - 余計なトップレベルキーの拒否
- `RecallHint` retry
  - validator 失敗時に 1 回だけ再生成
  - 2 回失敗したら `internal_failure`
- `secondary_intents`
  - `primary_intent` を上書きしない
  - 想起候補の rerank と section boost に使う
  - 返答方針の補助に使う
- `future_act`
  - `decision_generation` の内部結果で `reply / noop / future_act` を返せる
  - 通常 API の外向き結果は引き続き `reply / noop / internal_failure` に保つ
  - `future_act_summary` を trace と監査 event に残す
  - runtime-only の候補キューへ `create / update` できる
  - `candidate_id`、`dedupe_key`、`not_before`、`expires_at` を持てる
  - selected persona / memory / model 変更時にクリアできる
- 監査強化
  - memory consolidation failure を cycle trace と events に残す
  - reflective failure を `reflection_runs` と events に残す
- 記憶更新の保守化
  - compare key と evidence cycle を基準に扱う
  - scope 単位の雑な confirmed 昇格を避ける
  - `summary` は早すぎる confirmed を避ける

## 未着手: 自発起床ループ

`wake_policy` と `desktop_watch` は設定としては存在するが、実際の起床ループはまだない。

ここでやることは次である。

- `wake_policy` に基づく判断機会の生成
- `desktop_watch` を観測源として接続する
- 起床時に `reply / noop / future_act` を内部判断する
- 連続起床時の過剰反応を判断側で抑える

この段階では、外界行動の実行まではやらない。
まずは「自発的に話すか、保留するか」の内部判断だけを通す。

## 未着手: 記憶の高度化と運用硬化

後段でやることは次である。

- `relationship` と `self` の高次な長期変化分析
- `events` の限定ロードを使う精密根拠確認
- embedding 更新の非同期ジョブ化
- `reflective consolidation` の非同期ジョブ化
- migration の整理
- 長時間 smoke の整備
- 仕様が固まった後の回帰テスト整備

現時点では、仕様変更がまだ続いているので、新規テストの大量追加は後ろに置く。
ただし、`future_act` 導入後は最低限の回帰テストを足す。

## 既知の制約

ここは「バグ」ではなく、現在の設計上そうしている制約である。

- `event_evidence` は短い圧縮根拠であり、逐語引用や正確引用は保証しない
- `RecallHint` は長期記憶を読まず、現在観測と直近文脈だけから作る
- 複合意図は `primary_intent` 主軸で扱い、完全な同時最適化はしない
- `future_act` 候補キューはあるが、起床再評価と消費はまだない
- 自発起床と `desktop_watch` が未実装なので、現状は会話主導の MVP である

## 直近の実装順

次はこの順で進める。

1. `wake_policy` と起床判断ループを通す
2. `future_act` 候補の再評価、消費、期限切れ整理を起床側へ接続する
3. `desktop_watch` を観測源として繋ぐ
4. `relationship / self` の高次な反射再整理を絞って足す
5. 非同期ジョブ化と運用硬化へ進む
6. 仕様が固まった範囲から回帰テストを追加する

この順にする理由は、先に記憶の使い道を仕上げ、その後で自発判断へ広げるほうが安全だからである。

## 次のマイルストーン完了条件

次のマイルストーンは `future_act` 系である。
ここで完了とみなす条件は次とする。

- `decision_generation` が内部的には `reply / noop / future_act` を返せる
- 通常 API の外向き結果は変えない
- `future_act` 候補が trace に残る
- runtime-only 候補キューが動く
- memory 切り替え時の境界が壊れない
- 少なくとも最小の smoke / 回帰確認がある
