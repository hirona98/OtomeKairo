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
- wake observation API と background 起床スケジューラがあり、`wake_policy` の due 判定と保留意図の再評価で `reply / noop / pending_intent` を扱える
- `wake_scheduler_active` は server 内 scheduler の稼働状態を反映できる
- `desktop_watch` は event stream と capture-response を介して観測源として接続されている
- `desktop_watch` は接続中の `vision.desktop` capability を持つ唯一の console を自動で対象にする
- wake / `desktop_watch` の観測文には `source / active_app / window_title / locale` を正規化して入れられる
- `logs/stream` があり、`CocoroConsole` のログビューアーへ `Observation / Recall / Decision / Result / Memory` の短い live 要約を流せる
- embedding 更新と `reflective consolidation` は durable な background memory postprocess worker へ積み、通常の会話応答から外している
- `status.runtime_summary` で memory worker の稼働状態、queue 件数、処理中フラグを見られる
- inspection の `memory_trace` で `turn consolidation` と `vector_index_sync` / `reflective_consolidation` の後段状態を分けて追える
- `scripts/run_long_smoke.py` があり、isolated data dir と mock LLM 構成で background wake / `desktop_watch` / memory postprocess worker をまとめて回せる
- 同スクリプトは `--profile smoke / soak` で既定値 preset を切り替えられ、capture timeout recovery、`capture_client_id_mismatch`、`invalid_images`、`invalid_capture_error`、unknown request の無視、複数 `vision.desktop` client 接続時の停止/復帰、server 再起動時の memory postprocess 再投入も確認できる
- `desktop_watch` は reply 観測でだけ event と添付 image を返し、pending-intent / noop 境界では event を返さないことも同スクリプトで確認できる
- `--seed-data-dir` と `--editor-state-mode current` で既存 data dir を isolated copy して実運用寄り設定でも回せる

この前提では、MVP は完了済みとみなす。

- `desktop_watch` は MVP では `client_context` 主体とし、capture image の意味理解は MVP 外とする
- 長時間 smoke は完了済みとし、追加の長時間 soak 拡張は MVP 外とする
- 回帰テスト整備は MVP の完了条件に含めない

要するに、今は「会話MVP + 記憶基盤 + 自発判断 + 記憶後段非同期化 + `desktop_watch` 第一段 + 長時間 smoke 完了」までは通っており、残りは post-MVP の品質改善である。

## 補足資料

完了済み実装の細かい説明、コード構成、現実装が依存している前提は [03_実装済み詳細.md](03_実装済み詳細.md) へ移した。
この文書には、進捗判断と次アクションに必要な粒度だけを残す。

## MVP外: `desktop_watch` の高度化

`desktop_watch` は MVP としては第一段で完了とし、以降は拡張テーマとして扱う。

後続でやることは次である。

- capture image 自体を判断材料へ入れる
- 時刻帯や前景状況を使う判断補助を絞って足す
- `--profile soak` を土台に、background wake / desktop_watch の実時間が長い soak と追加 failure case へ広げる

## 次フェーズ: LLM寄せ移行

MVP 後の個別拡張は、いったん保留にする。
次フェーズでは、意味判断をできる限り LLM に寄せるための移行を優先する。

共通方針として、意味判断はできる限り LLM に寄せ、コードは契約、状態遷移、永続化、監査へ寄せる。詳細は [LLM判断優先方針.md](../design/20_LLM判断優先方針.md) を正とする。
移行計画の正本は [04_LLM寄せ移行計画.md](04_LLM寄せ移行計画.md) とする。

今回の前提では、回帰テスト整備はこのフェーズの対象外とする。

## 既知の制約

ここは「バグ」ではなく、現在の設計上そうしている制約である。

- `event_evidence` は短い圧縮根拠であり、逐語引用や正確引用は保証しない
- `RecallHint` は長期記憶を読まず、現在観測と直近文脈だけから作る
- 複合意図は `primary_intent` 主軸で扱い、完全な同時最適化はしない
- `desktop_watch` の画像そのものはまだ判断に入れておらず、現状は `client_context` 主体である
- background wake はあるが、複雑な時間帯制御や外界行動実行はまだ持たない

## LLM寄せ移行の実装順

現時点では 1 が完了し、次は 2 以降の順で進める。

1. `reflective consolidation` の summary 文面を LLM 化する
2. `event_evidence` の圧縮表現を LLM 化する
3. `RecallPack` の意味的 rerank / section 配置 / `conflicts` 文面を LLM 化する
4. `wake` の pending-intent 候補選択を LLM 化する
5. `desktop_watch` の観測意味理解を LLM 化する

この順にする理由は、文面生成寄りの層から先に LLM 化し、その後で recall 選別、自発再介入、画像観測へ広げるほうが、状態境界を壊さずに進めやすいからである。

## MVP 完了条件

今回の前提では、MVP は次を満たした時点で完了とみなす。

- wake / `desktop_watch` / memory postprocess worker の長時間運用で state 境界が壊れない
- 通常会話、wake、`desktop_watch` 第一段が共有判断パイプラインに入る
- 通常会話の `turn consolidation` と後段 worker が安定して動く
- inspection と runtime 表示で状態を追える

上の条件はすでに満たしているため、以降は MVP 完了後の改善フェーズとして扱う。
