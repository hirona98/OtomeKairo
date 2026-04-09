# MVP実装計画

## この文書の役割

この文書は、OtomeKairo の MVP 実装について「今どこまで通っていて、次に何をやるか」を管理するための計画書である。

ここでは次だけを扱う。

- 現在地
- 完了済みの実装範囲
- 既知の未完了項目
- 直近の実装順

長く残る契約、責務境界、データモデル、判断原則は `docs/design/` を正とする。
特に API 仕様は `docs/design/14_API仕様.md` と `docs/design/api/` 配下、記憶設計は `docs/design/memory/00_記憶設計方針.md` から辿る。
この文書は実装進捗の追跡を目的とし、設計の正本にはしない。

## 更新ルール

この計画書は、次の前提で更新する。

- 設計判断は `docs/design/` を正とする
- 実装状態はコードを正とする
- 実装や仕様を変えたら、同じ変更内で docs も更新する
- 古い予定ではなく、現時点の実装済み / 未完了 / 次アクションを優先して書く
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
- `turn consolidation`、構造レーン、連想レーン、`event_evidence`、`RecallPack` 接続までは通っている
- `reflective consolidation` は `self / user / relationship / topic` を対象に、複数ターンと既存 `memory_units` をまたぐ要約まで入っている
- `RecallHint` は validator 強化と 1 回再試行まで入っている
- `memory_interpretation` は厳密な構造化契約、`scope_type / scope_key` の正規化検証、1 回修復再試行で扱っている
- `secondary_intents` は rerank、section boost、返答方針補助に効く
- 第三者や固有名は `focus_scopes` ではなく `mentioned_entities` で扱う
- 保留意図は内部結果として扱え、trace に候補要約を残せる
- runtime-only の保留意図キューがあり、dedupe / update / expiry metadata を持てる
- wake observation API と background 起床スケジューラがあり、`wake_policy` の due 判定と保留意図の再評価で `reply / noop / future_act` を扱える
- `wake_scheduler_active` は server 内 scheduler の稼働状態を反映できる
- `desktop_watch` は event stream と capture-response を介して観測源として接続されている
- `desktop_watch` は `target_client_id` と `vision.desktop` capability を使って対象 console を選べる
- wake / `desktop_watch` の観測文には `source / active_app / window_title / locale` を正規化して入れられる
- `logs/stream` があり、`CocoroConsole` のログビューアーへ `Observation / Recall / Decision / Result / Memory` の短い live 要約を流せる

一方で、MVP 全体としてはまだ未完である。

- embedding 更新と `reflective consolidation` の完全非同期化は未完である
- `relationship` / `self` の反射再整理は第一段まで入ったが、要約精度の改善と高次な長期変化分析は未完である
- `desktop_watch` は `client_context` 主体であり、capture image 自体の意味理解は未完である
- 回帰テストはまだ本格整備していない

要するに、今は「会話MVP + 記憶基盤 + 自発判断の第一段」は通っており、「非同期化・運用硬化・精度向上」が残っている段階である。

## 現在の実装が依存している前提

ここは、設計の正本ではなく、現在の実装が依存している前提だけを置く。
API 結果種別や記憶契約の意味境界は `docs/design/` 側を正とする。

- 記憶と監査の正本は `memory.db` に置く
- 埋め込み検索は `sqlite-vec` を使う
- 構造レーンは半構造化カラム条件だけで引く
- 連想レーンはベクトル近傍検索だけで引く
- 文字列一致検索、`LIKE`、全文検索へのフォールバックは入れない
- `server_state.json` は当面、設定定義の正本として残す

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
- `src/otomekairo/event_stream.py`
  - WebSocket frame handling
  - event stream registry
- `src/otomekairo/log_stream.py`
  - log stream registry
  - recent live log replay
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
  - 設計上の `episodes` に相当する現行テーブル
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
  - `episodes` 相当データの作成
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
  - `memory_units` と `episodes` 相当データの埋め込み index
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
  - 前回 `updated` だった run 以降を観測窓として扱う
  - `self_change` / `relationship_change` を trigger reason にできる
  - `self / user / relationship / topic` の長期要約を作れる
  - `support_cycle_count` を使い、`self / relationship` の summary を単発ノイズで確定しにくくしている

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
- 保留意図
  - `decision_generation` の内部結果で `reply / noop / future_act` を返せる
  - `future_act` は現実装の内部 kind 名であり、設計上は保留意図として扱う
  - 通常 API の外向き結果は引き続き `reply / noop / internal_failure` に保つ
  - `future_act_summary` を trace と監査 event に残す
  - runtime-only の候補キューへ `create / update` できる
  - `candidate_id`、`dedupe_key`、`not_before`、`expires_at` を持てる
  - selected persona / memory / model 変更時にクリアできる
- wake observation
  - `POST /api/observations/wake` で起床判断を走らせられる
  - `wake_policy` が `disabled` なら `noop`
  - `interval_minutes` に達していなければ `noop`
  - due な保留意図があれば再評価し、`reply` なら候補を消費する
  - cooldown と同一 `dedupe_key` の直近 reply を見て過剰反応を抑える
  - `client_context.source / active_app / window_title / locale` を観測文へ正規化できる
- background wake scheduler
  - server 起動時に background スレッドを開始する
  - `wake_policy.mode=interval` のときだけ自動起床を有効扱いにする
  - wake API と同じ判断経路で wake cycle を実行する
  - manual wake と background wake の二重実行は直列化する
  - runtime-only queue と wake runtime state は lock 付きで扱う
- desktop_watch
  - `GET /api/events/stream` で client が `hello` を送り、`client_id` と `caps` を登録できる
  - `desktop_watch.target_client_id` に対して `vision.capture_request` を送れる
  - `POST /api/v2/vision/capture-response` を受けられる
  - `desktop_watch.interval_seconds` に基づく background loop がある
  - capture result の `client_context` を使って `trigger_kind="desktop_watch"` の判断 cycle を実行できる
  - `reply` 時は `desktop_watch` event と添付 image を target client へ返せる
- 監査強化
  - memory consolidation failure を cycle trace と events に残す
  - reflective failure を `reflection_runs` と events に残す
- 記憶更新の保守化
  - compare key と evidence cycle を基準に扱う
  - scope 単位の雑な confirmed 昇格を避ける
  - `summary` は早すぎる confirmed を避ける

## 完了済み: 自律的な自発起床ループの第一段

この段階で、最低限の自律起床は通っている。

- server 起動時に background 起床スケジューラが立ち上がる
- `wake_policy.mode=interval` の間だけ、自動で wake 機会を作る
- wake API と background wake は同じ内部判断経路を通る
- due な保留意図があれば再評価し、`reply` なら候補を消費する
- cooldown と同一 `dedupe_key` の直近 reply で過剰反応を抑える
- wake cycle は `trigger_kind="wake"` で inspection に残る

この段階でも、外界行動の実行まではやらない。
自発的に話すか、保留するかの内部判断に留める。

## 完了済み: `desktop_watch` 接続の第一段

この段階で、CocoroConsole とつながる最小の `desktop_watch` は通っている。

- `events/stream` WebSocket がある
- client の `hello(client_id, caps)` を受けられる
- `vision.capture_request` と `vision/capture-response` が往復する
- `target_client_id` と `vision.desktop` capability で対象 client を絞る
- capture result の `active_app / window_title / locale` を `client_context` として判断へ渡せる
- `desktop_watch` 観測文に `active_app / window_title / locale / image_count` を正規化して入れられる
- `reply` になった場合は `desktop_watch` event を stream へ返せる

この段階でも、画像内容の意味理解まではまだやらない。
MVP では capture image を通知添付に使い、判断は `client_context` 主体に留める。

## 未完了: `desktop_watch` の高度化

`desktop_watch` は第一段まで入ったが、まだ詰める余地がある。

後続でやることは次である。

- capture image 自体を判断材料へ入れる
- 時刻帯や前景状況を使う判断補助を絞って足す
- background wake / desktop_watch の長時間 smoke を整備する

## 未完了: 記憶の高度化と運用硬化

後段でやることは次である。

- `relationship` と `self` の要約精度向上と、より高次な長期変化分析
- `events` の限定ロードを使う精密根拠確認
- embedding 更新の非同期ジョブ化
- `reflective consolidation` の非同期ジョブ化
- migration の整理
- 長時間 smoke の整備
- 仕様が固まった後の回帰テスト整備

現時点では、仕様変更がまだ続いているので、新規テストの大量追加は後ろに置く。
ただし、wake / 保留意図周りの最低限の回帰確認は後ろへ逃がしすぎない。

## 既知の制約

ここは「バグ」ではなく、現在の設計上そうしている制約である。

- `event_evidence` は短い圧縮根拠であり、逐語引用や正確引用は保証しない
- `RecallHint` は長期記憶を読まず、現在観測と直近文脈だけから作る
- 複合意図は `primary_intent` 主軸で扱い、完全な同時最適化はしない
- `desktop_watch` の画像そのものはまだ判断に入れておらず、現状は `client_context` 主体である
- background wake はあるが、複雑な時間帯制御や外界行動実行はまだ持たない

## 直近の実装順

次はこの順で進める。

1. embedding 更新と `reflective consolidation` の非同期ジョブ化を進める
2. background wake / `desktop_watch` の長時間 smoke と state 境界確認を整備する
3. `relationship / self` の要約精度向上と精密根拠確認を進める
4. 仕様が固まった範囲から回帰テストを追加する

この順にする理由は、先に同期経路の重い処理を外して長時間運用を安定させ、その上で記憶品質を詰めるほうが安全だからである。

## 次のマイルストーン完了条件

次のマイルストーンは、非同期化と運用硬化である。
ここで完了とみなす条件は次とする。

- embedding 更新と reflection が段階的に非同期化される
- wake / `desktop_watch` の長時間運用で state 境界が壊れない
- `relationship / self` の第一段要約が運用を塞がない形で安定して使える
- 少なくとも最小の smoke / 回帰確認がある
