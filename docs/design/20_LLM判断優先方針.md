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

意味判断であり、かつ外部安全性や正本整合性を直接壊さないなら、まず LLM へ寄せる。

## LLM に寄せる対象

原則として、次は LLM を第一候補にする。

- 観測の解釈
- 想起入口の整理
- 想起候補の意味的優先付け
- 要約文面の生成
- 記憶候補の意味的整理
- reply 文面の生成
- 背景観測の意味づけ

将来、現在ロジックで持っている処理であっても、次の条件を満たす処理は LLM 化の検討対象とする。

- 出力を構造化契約で縛れる
- 失敗時に限定的に切り離せる
- state 遷移の正否を直接決めない
- デバッグや inspection で追跡できる

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
- 失敗時に意味補正ロジックを重ねて挙動を曖昧にする
- 安全境界や実行権限を LLM に持たせる

## 現時点の優先移行候補

現在の実装で、LLM に寄せる優先度が高いのは次である。

1. `reflective consolidation` の `summary_text`
2. `event_evidence` の圧縮表現
3. 想起候補の意味的 rerank と section 選別
4. `wake` の pending-intent 候補選択
5. `desktop_watch` の capture image を含む観測意味理解

## 個別設計との関係

この文書は repo 全体の判断原則を定める。

個別機能の詳細は、各設計書で次を具体化する。

- LLM に渡す入力
- 出力契約
- validator
- failure 時の閉じ込め方
- inspection と監査への露出

`reflective consolidation` の summary 文面については [07_内省要約のLLM生成.md](memory/07_内省要約のLLM生成.md) を正とする。
repo 全体の移行順は [04_LLM寄せ移行計画.md](../plan/04_LLM寄せ移行計画.md) を正とする。
