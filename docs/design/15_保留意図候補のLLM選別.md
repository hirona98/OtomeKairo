# 保留意図候補の LLM 選別

## 背景

現在の `wake` / `desktop_watch` では、runtime-only の `pending_intent_candidates` から期限内かつ due な候補を拾ったあと、
`_select_due_pending_intent_candidate()` が `updated_at / created_at` の昇順で 1 件だけ選んでいる。

現行コードでは、少なくとも次が fixed logic である。

- 候補の最終選択が oldest-first である
- `intent_summary` や `reason_summary` の意味差を選択へ使えていない
- `desktop_watch` の `active_app / window_title / locale` が候補選択に効いていない
- 複数候補が並んだとき、現在観測との噛み合いより時刻順が優先される
- `wake` では、最近 reply 済みで抑制される候補が先頭に来ると、他候補があっても起床機会を無駄にしやすい

この方式は MVP 段階では壊れにくかったが、本命実装としては次の問題が残る。

- 自発再介入が「いま自然な候補」ではなく「いちばん古い候補」へ寄りやすい
- `desktop_watch` の文脈が選別前に使われず、見えている状況と無関係な保留候補を差し込みやすい
- 複数の保留候補があるとき、どれを再評価に乗せるかの意味判断がコードに閉じる
- repo 全体の LLM 判断優先方針に対して、自発再介入の入口だけが固定ロジックのまま残る

そのため、**保留意図候補の最終選択だけ** を LLM へ分離する。

## 採用方針

OtomeKairo では、保留意図キュー全体を LLM 任せにはしない。

- 候補の作成 / 更新 / 期限切れ削除はコードで行う
- `memory_set_id`、`not_before`、`expires_at`、wake の interval due、cooldown はコードで守る
- 同じ `dedupe_key` に対する過剰反応抑制もコードで守る
- `desktop_watch` の capture protocol と client 選択はコードで守る
- **すでに eligible になった候補群の中から、いま再評価に乗せる 1 件を選ぶか、今回は選ばないか** だけを LLM に任せる

要するに、`どの候補が選択対象になりうるか` はコードが決め、`いまどれを前に出すと自然か` だけを LLM に任せる。

## 目的

- `wake` / background wake / `desktop_watch` の保留候補選択を oldest-first から意味的選別へ寄せる
- `trigger_kind`、`client_context`、直近会話、`intent_summary` を候補選択に効かせる
- 候補があっても「今回は選ばない」を正常系として扱えるようにする
- deterministic な queue 境界、scheduler、cooldown、監査構造は壊さない

## 対象範囲

この設計の対象は次だけである。

- eligible な保留意図候補群の最終選択
- trigger ごとの差分を含む source pack 構築
- LLM 入出力契約
- `wake` と `desktop_watch` への選択結果の適用
- 失敗時の扱いと inspection / audit への露出

想定する主な変更対象ファイルは次である。

- `src/otomekairo/service_spontaneous.py`
- `src/otomekairo/service.py`
- `src/otomekairo/defaults.py`
- `src/otomekairo/service_common.py`
- `src/otomekairo/llm.py`
- `src/otomekairo/llm_prompts.py`
- `src/otomekairo/llm_contracts.py`
- `src/otomekairo/llm_mock.py`

## 対象外

この設計では次は変えない。

- `pending_intent` 自体の生成契約
- `pending_intent_candidates` の upsert / dedupe / expiry 管理
- wake scheduler の interval 制御
- `desktop_watch` の画像意味理解
- `decision_generation` / `expression_generation`
- 記憶更新と `turn consolidation`

## 基本構成

保留意図候補の LLM 選別は、次の 5 段に分ける。

1. コードが trigger ごとの deterministic 条件で eligible 候補群を作る
2. コードが request-local な `candidate_ref` を持つ source pack を作る
3. `pending_intent_selection` role が `candidate_ref | none` を返す
4. コードが ref を実 candidate に戻す
5. trigger ごとの流れに応じて、観測文へ差し込むか、今回は差し込まないかを決める

ここで重要なのは、LLM が **候補の外側を増やさない** ことである。
新しい candidate、未知の trigger、未定義の結果種別を作ってはいけない。

## trigger ごとにコード側へ残す境界

### `wake` / background wake

- `wake_policy` の due 判定
- global cooldown 判定
- `memory_set_id` 一致
- `not_before` / `expires_at` の検証
- 同じ `dedupe_key` の recent reply 抑制
- `selected_candidate_ref=none` のときに `wake_noop` へ落とすこと

### `desktop_watch`

- `vision.desktop` client の単独選択
- capture request / response / timeout
- `desktop_watch.enabled` と interval 制御
- `memory_set_id` 一致
- `not_before` / `expires_at` の検証
- `selected_candidate_ref=none` でも通常の `desktop_watch` 観測としてパイプラインを継続すること

## 追加する論理 role

この機能では、モデルプリセットに `pending_intent_selection` という論理 role を追加する。

この role の責務は次だけである。

- eligible な保留意図候補群から、今の trigger で再評価に乗せる 1 件を選ぶ
- どの候補も自然でなければ `none` を返す
- その選択理由を短い 1 文で返す

この role を `observation_interpretation` や `decision_generation` から分ける理由は次である。

- 自発再介入の入口選別と、その後の本体判断を分離できる
- `wake` / `desktop_watch` の候補選びだけを回帰確認しやすい
- pending 候補管理の deterministic 部分を service 側へ残したまま、意味的比較だけを差し替えられる

## source pack の設計

LLM に渡すのは runtime candidate の生オブジェクトではなく、request-local ref を振った source pack とする。

最低限、次を含める。

```json
{
  "trigger_kind": "desktop_watch",
  "observation_context": {
    "source": "desktop_watch",
    "active_app": "Discord",
    "window_title": "DM with Hiro",
    "locale": "ja-JP",
    "image_count": 1
  },
  "recent_turns": [
    {
      "role": "assistant",
      "text": "無理ならまたあとで少し聞かせてください。"
    },
    {
      "role": "user",
      "text": "今日は少し疲れてる。また今度。"
    }
  ],
  "selection_policy": {
    "allow_none": true,
    "max_selected_candidates": 1
  },
  "candidates": [
    {
      "candidate_ref": "candidate:1",
      "intent_kind": "conversation_follow_up",
      "intent_summary": "体調の話の続きを短く確認したい。",
      "reason_summary": "前回は疲れている様子だったので、あとで改めて様子を見る判断にした。",
      "minutes_since_created": 18,
      "minutes_since_updated": 6,
      "minutes_until_expiry": 221
    },
    {
      "candidate_ref": "candidate:2",
      "intent_kind": "conversation_follow_up",
      "intent_summary": "週末の予定の返事を後で確認したい。",
      "reason_summary": "相手が移動中だったため、その場では深掘りしなかった。",
      "minutes_since_created": 42,
      "minutes_since_updated": 42,
      "minutes_until_expiry": 197
    }
  ]
}
```

入力の原則は次である。

- `candidate_ref` は request-local 参照であり、runtime の `candidate_id` をそのまま渡さない
- `source_cycle_id` や `dedupe_key` は LLM に渡さない
- `recent_turns` は現在 pipeline と同じ取得窓から、最大 4 発話までを短くクランプして渡す
- `desktop_watch` では image 本体を渡さず、`client_context` の text 項目と `image_count` だけを渡す
- `minutes_since_*` / `minutes_until_expiry` は補助情報であり、古さだけで選ばせない

## LLM 出力契約

LLM の出力は JSON object 1 個に固定する。

```json
{
  "selected_candidate_ref": "candidate:1",
  "selection_reason": "前景の文脈と体調確認の保留意図が噛み合っており、今は短く再開する自然さがある。"
}
```

候補を選ばないときは次の形にする。

```json
{
  "selected_candidate_ref": "none",
  "selection_reason": "今の観測だけでは、どの保留候補を前に出しても自然さが弱い。"
}
```

契約は次とする。

- 必須キーは `selected_candidate_ref` と `selection_reason` の 2 つ
- `selected_candidate_ref` は source pack 内に存在する `candidate_ref` か `"none"` のいずれか
- 余計なトップレベルキーは持たない
- `selection_reason` は 1 文、改行なし、内部識別子なし
- 候補を選ばない場合でも `selection_reason` は必須とする

## プロンプト方針

system prompt では、少なくとも次を明示する。

- あなたは保留意図候補の再評価入口だけを選ぶ
- 候補外のものを足さない
- 最大 1 件だけ選ぶ。弱ければ `none` を返す
- oldest-first で選ばない
- `trigger_kind` と `observation_context` に現在の自然さがあるかを優先する
- `wake` では慎重に選び、自然でなければ `none` に倒す
- `desktop_watch` では `active_app / window_title / locale / image_count` だけを手掛かりにする
- image の意味理解はまだ行わない

user prompt では、trigger、観測文脈、直近会話、候補群を構造化でそのまま渡す。

## trigger ごとの適用

### `wake` / background wake

1. due / cooldown をコードで確認する
2. eligible 候補が無ければ LLM を呼ばず `wake_noop` にする
3. LLM が `selected_candidate_ref=none` を返したら `wake_noop` にする
4. 候補が選ばれたときだけ、その候補要約を含む観測文を組み立てて共有判断パイプラインへ入る

### `desktop_watch`

1. capture 後に `client_context` を正規化する
2. eligible 候補があれば selector を呼ぶ
3. 候補が選ばれたときだけ、その候補要約を `desktop_watch` 観測文へ足す
4. `selected_candidate_ref=none` でも、`desktop_watch` 観測自体は通常どおり共有判断パイプラインへ流す

## 処理フロー

実装時の flow は次とする。

1. runtime queue から未失効候補を読む
2. trigger ごとの deterministic 条件で eligible 候補を絞る
3. source pack 用に `candidate_ref` を振る
4. `pending_intent_selection` role を呼ぶ
5. parse / contract が崩れたときだけ repair prompt で 1 回だけ再試行する
6. `selected_candidate_ref` を実 candidate に戻す
7. trigger ごとの規則に従って `wake_noop` または共有判断パイプラインへ進む
8. cycle trace と audit event に結果を残す

## 失敗時の扱い

この設計では oldest-first への fallback を入れない。

- eligible 候補が 0 件のときは、selector を呼ばず trigger 既定の挙動へ進む
- selector を呼んだあとで repair 1 回後も parse / contract を満たせなければ、その cycle は `internal_failure` にする
- `wake` でも `desktop_watch` でも、選別失敗を silent に `none` 扱いしない
- `selected_candidate_ref=none` は正常系であり failure ではない
- failure event として `pending_intent_selection_failure` を残す

## 観測と監査

inspection で追いやすくするため、`cycle_trace.observation_trace` に `pending_intent_selection` を追加する。

最低限、次を持つ。

- `candidate_pool_count`
- `eligible_candidate_count`
- `selected_candidate_ref`
- `selected_candidate_id`
- `selection_reason`
- `result_status`
- `failure_reason`

`selected_candidate_id` は trace 上の内部確認用であり、LLM 入出力には使わない。

`decision_trace.pending_intent_candidate_summary` と `result_trace.pending_intent_summary` は、
引き続き **新しい保留意図を queue へ作成 / 更新した結果** を表す。
今回追加する `pending_intent_selection` trace とは責務を混ぜない。

監査 event として `pending_intent_selection_failure` を追加する。
この event は `failure_stage` と `failure_reason` を持ち、cycle 単位で選別失敗を追えるようにする。

## `05_判断と行動.md` との関係

`docs/design/05_判断と行動.md` は、保留意図の意味境界、自律判断、過剰介入抑制を定める上位設計である。

この文書は、その中の **保留意図候補をどれだけ再評価に乗せるか** を LLM へ寄せるときの実装設計として次を具体化する。

- role
- source pack
- structured output 契約
- trigger ごとの適用差分
- failure の扱い
- inspection と監査への出し方

## 移行計画との関係

この文書は、Phase 4 の `wake` / `desktop_watch` における保留意図候補選択の機能設計である。
上位の移行順と全体方針は `docs/plan/04_LLM寄せ移行計画.md` を正とする。
