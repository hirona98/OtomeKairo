# 研究メモから引き出した採用候補

<!-- Block: Purpose -->
## このドキュメントの役割

- このドキュメントは、既存の研究メモから OtomeKairo で採用価値が高い要素だけを抜き出した統合メモである
- 比較の詳細は `docs/note/memory_research_notes.md` と `docs/note/autonomous_embodiment_research_notes.md` を見る
- ここでは「このプロジェクトで何を使うか」に絞って整理する
- 最終設計へ昇格する前の中間整理なので、`docs/note/` に置く

<!-- Block: Core View -->
## 採用方針の中心

- 記憶だけを強くするのではなく、`観測`, `注意`, `判断`, `行動`, `反省`, `記憶育成` を一体で設計する
- LLM は人格全体そのものではなく、認知補助として使う
- 状態の正本は人格ランタイムだけが持ち、外部入力や LLM 出力で直接書き換えない
- 高位の意図形成と低位の実行制御を分離する
- 会話中心ではなく、`観測イベント -> 行動` の一般化されたループで設計する

<!-- Block: Priority 1 -->
## 最優先で採用する要素

- `認知ループの明示`
  - `observe -> attend -> decide -> act -> reflect -> consolidate` を中心ループとして固定する
  - 今の設計では `attend` と `reflect` が弱いので、独立した責務にする

- `高位と低位の分離`
  - LLM は `意図形成`, `候補生成`, `要約`, `反省` までに限定する
  - `実行可否判定`, `デバイス制御`, `永続化` は決定論的なモジュールで行う
  - これにより、LLM に直接デバイスを触らせない

- `記憶階層の明示`
  - `working memory`, `episodic memory`, `semantic memory`, `affective memory`, `retrieval indexes` を分ける
  - `working` は短周期の作業領域、`episodic` は出来事、`semantic` は育った知識、`affective` は感情痕跡と持続気分に分ける
  - `sqlite-vec` は記憶本体ではなく検索索引として扱う

- `反省の常設`
  - 長周期ループに `reflection` を置く
  - 失敗やズレを `reflection_notes`, `retry_hint`, `avoid_pattern` のような形で次回へ持ち越す

- `instruction priority`
  - `system policy > runtime policy > external input > tool output` の優先順位を持つ
  - Web 入力、SNS 入力、Web 検索結果で中核ポリシーを直接上書きさせない

<!-- Block: Priority 2 -->
## 次に採用する要素

- `attention`
  - 何を優先して見るかを、記憶と別の状態として持つ
  - `attention_targets` を `self_state` または `world_state` の一部として明示する

- `skill registry`
  - 繰り返し成功した行動列を、単発イベントではなく再利用可能なスキルとして保存する
  - `task_state` とは別の領域に持ち、再利用時は高位計画の候補にする

- `affordance-aware world_state`
  - `見える`, `近づける`, `避ける`, `操作可能` のような空間的・行動的制約を `world_state` に持つ
  - 視点変更や移動は、空間状態を更新する正式な行動として扱う

- `逐次補正`
  - 長い計画を一括で最後まで流さず、短い行動単位で `act -> observe -> adjust` を挟む
  - `1 回の正しい計画` を前提にしない

- `マルチモーダル観測の統一`
  - テキスト、カメラ、マイク、ネットワーク応答を、モダリティ付きの `observation_frame` に正規化する
  - テキスト入力だけを特別扱いしない

<!-- Block: Memory Specific -->
## 記憶設計で採用する要素

- `同期で思い出す / 非同期で育てる`
  - `CocoroGhost_システムフロー図` の中核であり、そのまま使える
  - これを `短周期ループ / 長周期ループ` に対応づける

- `events を中心にしたエピソード記録`
  - `events`, `event_links`, `event_threads` は、会話だけでなく行動と観測を含む出来事記録へ拡張する
  - 直列の行動文脈と因果の両方を持てるようにする

- `state の分割`
  - 現在の広い `state` 的概念は、`self_state / body_state / world_state / drive_state` に分ける
  - 現在状態と長期知識を同じ器に入れ続けない

- `忘却と再強化`
  - 記憶は永遠に固定せず、`importance`, `memory_strength`, `last_accessed_at` を使って減衰させる
  - 再参照や再利用で再強化する

- `感情の二層化`
  - 出来事に紐づく感情痕跡は `event_affects`
  - 背景の気分や持続感情は `self_state` の持続状態
  - これを同じ項目で持たない

<!-- Block: Action Specific -->
## 自律行動設計で採用する要素

- `action proposal と action command の分離`
  - LLM が出すのは `action proposal`
  - 実行するのは、検証済みの `action_command`
  - 候補と実行を同一視しない

- `実行可否の検証`
  - `SayCan` 的に、もっともらしい行動と実際に可能な行動を分ける
  - 実行前に `policy` と `actuator` 側で能力・安全・現在状態を確認する

- `実行中フィードバック`
  - `Inner Monologue` 的に、行動中の観測変化を次の判断に戻す
  - 1 行動を撃ったら終わりにしない

- `統合点としての gateway`
  - 各センサーや API を人格コアから直接呼ばず、`gateway` に抽象境界を置く
  - 実世界統合は `gateway` と `infra` に閉じ込める

<!-- Block: Concrete Adoption -->
## 設計書へ反映する具体項目

- `docs/30_design_breakdown.md` に `attend` を独立した処理段として追加する
- `docs/30_design_breakdown.md` の長周期ループに `reflection` を追加する
- `docs/30_design_breakdown.md` に `skill_registry` を追加する
- `docs/30_design_breakdown.md` の `world_state` に `affordances / constraints / attention_targets` を追加する
- `docs/30_design_breakdown.md` に `instruction priority` を追加する
- `docs/30_design_breakdown.md` の永続化分解に `working_memory` と `reflection_notes` を追加する
- `docs/10_target_architecture.md` の中心ループを `observe -> attend -> decide -> act -> reflect` に寄せる

<!-- Block: Things Not To Adopt -->
## この時点では採用しない要素

- 巨大な end-to-end 学習モデルの再現
- LLM による直接デバイス制御
- 長大な単発計画の一括実行
- 外部入力で中核方針を直接上書きする設計
- テキストだけを特別扱いする入力モデル

<!-- Block: Final Summary -->
## 最終まとめ

- 本プロジェクトで採用価値が最も高いのは、`認知ループ`, `高位低位分離`, `記憶階層`, `反省`, `instruction priority` の 5 本柱である
- その次に、`attention`, `skill_registry`, `affordance-aware world_state`, `逐次補正`, `マルチモーダル観測統一` を入れるのがよい
- これらを入れることで、会話システムではなく、身体性を持つ人格個体としての設計に近づく
