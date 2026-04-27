# LLM 判断優先方針

## 目的

OtomeKairo では、できる限り LLM が意味判断を担う方向を採る。

ここでいう意味判断とは、次のようなものを指す。

- 何に注目すべきか
- 何を思い出すべきか
- どう要約すべきか
- どういうニュアンスで伝えるべきか
- どの候補が今の文脈により合っているか

一方で、実行境界や状態遷移まで LLM に丸投げすることはしない。

## 基本原則

新しい処理を設計するときは、まず次を考える。

- これは意味判断か
- それとも契約、境界、永続化、権限制御か

意味判断であり、かつ次を満たすなら、まず LLM を第一候補にする。

- 出力を構造化契約で縛れる
- コード側で validator を置ける
- 失敗時の影響範囲を 1 判断サイクル、1 scope、1 job の単位に閉じ込められる
- 失敗時に unsafe な実行、壊れた state 遷移、壊れた永続化を起こさない
- inspection と audit で失敗理由を追える

## LLM を第一候補にする対象

原則として、次は LLM を第一候補にする。

- 観測の解釈
- 想起入口の整理
- 想起候補の意味的優先付け
- 要約文面の生成
- 記憶候補の意味的整理
- reply 文面の生成
- 背景観測の意味づけ

現在ロジックで持っている処理でも、次の条件を満たす処理は LLM 採用の検討対象とする。

- 出力を構造化契約で縛れる
- コード側で validator を置ける
- 失敗時の影響範囲を閉じ込められる
- state 遷移の正否を直接決めない
- デバッグや inspection で追跡できる

## LLM 処理の分類

OtomeKairo では、LLM を使う意味判断を次の 2 種類に分ける。

### サイクル中核の意味判断

その判断サイクルを成立させる中心処理である。
失敗した場合は、そのサイクルを `internal_failure` として終了する。

- `RecallHint`
- `RecallPack` の意味的選別
- `pending_intent` 候補選別
- `decision_generation`

### 補助生成

派生情報や補助表現を作る処理である。
失敗した場合は、その部分だけを落とし、他の処理は継続する。

- `event_evidence` の圧縮
- `reflective consolidation` の `summary_text` 生成

## コードに残す対象

次は、原則としてコード側へ残す。

- API 契約と validator
- capability と権限境界
- 接続管理、認証、資格情報の扱い
- scheduler、queue、retry、timeout
- persistence と transaction
- `create / refine / reinforce / supersede / revoke / dormant` のような状態遷移
- rate limit や件数上限のような deterministic 制約
- failure の監査記録

要するに、LLM は「意味を決める」側であり、コードは「壊れない範囲を決める」側である。

## 推奨アーキテクチャ

OtomeKairo では、次の分離を基本形にする。

1. コードが入力候補と境界条件を整理する
2. LLM が意味判断や要約を返す
3. コードが契約検証を行う
4. コードが state 遷移、永続化、監査を行う

この順序を崩さない。

## やらないこと

次は採らない。

- LLM の自由文をそのまま state 遷移へ使う
- validator を置かずに structured output を信じる
- 失敗を旧ロジック fallback で隠す
- 失敗を silent に正常系へ丸める
- 安全境界や実行権限を LLM に持たせる

## 現在の主要適用済み項目

現在の実装で、意味判断を LLM で行っている主要項目は次である。

1. `reflective consolidation` の `summary_text`
2. `event_evidence` の圧縮表現
3. 想起候補の意味的 rerank と section 選別
4. `wake` / `desktop_watch` の pending-intent 候補選択

`vision.capture` の image payload を使う観測意味理解は、`desktop_watch` と通常会話画像入力に対する第一段を適用済みとする。
この段階では raw image payload を保持せず、短い観測要約だけを判断入力へ渡す。

`world_state` 更新では、画面要約に加えて `external_service / body / device / schedule` の短い structured context と wake の pending-intent 時刻ヒントを source pack に入れ、状態候補の意味抽出を LLM に寄せる。

## 個別設計との関係

この文書は repo 全体の判断原則を定める。
限定的な LLM 補助処理の共通契約は [19_LLM補助契約共通.md](19_LLM補助契約共通.md) を正とする。

個別機能の詳細は、各設計書で次を具体化する。

- LLM に渡す入力
- 出力契約
- validator
- failure 時の閉じ込め方
- inspection と監査への露出

`reflective consolidation` の summary 文面については [07_内省要約のLLM生成.md](memory/07_内省要約のLLM生成.md) を正とする。
現在地の整理は [01_現行計画.md](../plan/01_現行計画.md) を正とする。
