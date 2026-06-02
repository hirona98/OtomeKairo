# 保留意図候補の LLM 選別

## 位置づけ

この文書は、保留意図候補の最終選別を定める。
実装状態は `src/` と smoke 結果を正とする。
LLM 補助処理の共通境界、source pack、出力、failure、inspection の共通ルールは [19_LLM補助契約共通.md](19_LLM補助契約共通.md) を正とする。

## 目的

`wake` と定期起床では、runtime-only の `pending_intent_candidates` から期限内かつ due な候補を拾ったあと、どの候補を今の判断へ戻すかを決める必要がある。

この選別は fixed logic の時刻順ではなく、`trigger_kind`、直近会話、`intent_summary`、`reason_summary`、現在の `drive_state` に照らした意味判断で行う。
ただし、保留意図キュー全体を LLM 任せにはしない。

## 採用方針

OtomeKairo では、次をコードで守る。

- 候補の作成、更新、期限切れ削除
- `memory_set_id` の一致
- `not_before`、`expires_at`、wake の interval due
- 同じ `dedupe_key` に対する recent speech 抑制
- LLM が返した `candidate_ref` と実 candidate の照合

LLM に任せるのは、eligible な候補群の中から今再評価に乗せる 1 件を選ぶか、今回は選ばないかだけである。
`not_before`、`expires_at`、cooldown の比較は offset 付きローカル timestamp と duration でコードが行う。
LLM には時刻の生活文脈要約だけを渡す。

## 対象範囲

この設計の対象は次だけである。

- eligible な保留意図候補群の最終選択
- trigger ごとの差分を含む source pack 構築
- LLM 入出力契約
- wake と定期起床への選択結果の適用
- 失敗時の扱いと inspection / audit への露出

## 対象外

この設計では次は変えない。

- `pending_intent` 自体の生成契約
- `pending_intent_candidates` の upsert / dedupe / expiry 管理
- wake scheduler の interval 制御
- capability result の follow-up
- `decision_generation` / `expression_generation`
- 記憶更新と `turn consolidation`

## 基本構成

保留意図候補の LLM 選別は、次の 5 段に分ける。

1. コードが trigger ごとの deterministic 条件で eligible 候補群を作る
2. コードが request-local な `candidate_ref` を持つ source pack を作る
3. `pending_intent_selection` role が `candidate_ref | none` を返す
4. コードが ref を実 candidate に戻す
5. 選択結果に応じて入力文へ差し込むか、今回は差し込まないかを決める

LLM は候補の外側を増やさない。
新しい candidate、未知の trigger、未定義の結果種別を作らない。

## trigger ごとにコード側へ残す境界

### `wake` / 定期起床

- `wake_policy` の due 判定
- global cooldown 判定
- `memory_set_id` 一致
- `not_before` / `expires_at` の検証
- 同じ `dedupe_key` の recent speech 抑制
- `selected_candidate_ref=none` のときに `wake_noop` へ落とすこと

## 追加する論理 role

この機能では、モデルプリセットに `pending_intent_selection` という論理 role を追加する。

この role の責務は次だけである。

- eligible な保留意図候補群から、今の trigger で再評価に乗せる 1 件を選ぶ
- どの候補も自然でなければ `none` を返す
- その選択理由を短く返す

この role を `input_interpretation` や `decision_generation` から分ける理由は次である。

- 自発再介入の入口選別と、その後の本体判断を分離できる
- wake 系 trigger の候補選びを回帰確認しやすい
- pending 候補管理の deterministic 部分を service 側へ残したまま、意味的比較だけを差し替えられる

## source pack の設計

LLM に渡すのは runtime candidate の生オブジェクトではなく、request-local ref を振った source pack とする。

最低限、次を含める。

```json
{
  "trigger_kind": "background_wake",
  "input_context": {
    "source": "background_wake_scheduler",
    "drive_state_summary": [
      {
        "drive_kind": "follow_through",
        "summary_text": "前回保留した話題を自然なタイミングで再確認したい。"
      }
    ]
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
    }
  ]
}
```

入力の原則は次である。

- `candidate_ref` は request-local 参照であり、runtime の `candidate_id` をそのまま渡さない
- `source_cycle_id` や `dedupe_key` は LLM に渡さない
- `recent_turns` は `prompt_window` で取得した現在 pipeline の直近会話候補から、最大 4 発話だけを渡す
- `minutes_since_*` / `minutes_until_expiry` は補助情報であり、古さだけで選ばせない

## LLM 出力契約

LLM の出力は JSON object 1 個に固定する。

```json
{
  "selected_candidate_ref": "candidate:1",
  "selection_reason": "直近の会話と保留意図の継続性があり、今は短く再開する自然さがある。"
}
```

候補を選ばないときは次の形にする。

```json
{
  "selected_candidate_ref": "none",
  "selection_reason": "今の起床機会だけでは、どの保留候補を前に出しても自然さが弱い。"
}
```

契約は次とする。

- 必須キーは `selected_candidate_ref` と `selection_reason` の 2 つ
- `selected_candidate_ref` は source pack 内に存在する `candidate_ref` か `"none"` のいずれか
- 余計なトップレベルキーを持たない
- `selection_reason` は簡潔にし、改行なし、内部識別子なし
- 候補を選ばない場合でも `selection_reason` は必須とする

## プロンプト方針

system prompt では、少なくとも次を明示する。

- 保留意図候補の再評価入口だけを選ぶ
- 候補外のものを足さない
- 最大 1 件だけ選ぶ。弱ければ `none` を返す
- oldest-first で選ばない
- `trigger_kind` と `input_context` に現在の自然さがあるかを優先する
- wake では慎重に選び、自然でなければ `none` に倒す

user prompt では、trigger、入力文脈、直近会話、候補群を構造化でそのまま渡す。

## trigger ごとの適用

### `wake` / 定期起床

1. due / cooldown をコードで確認する
2. eligible 候補が無ければ LLM を呼ばず `wake_noop` にする
3. LLM が `selected_candidate_ref=none` を返したら `wake_noop` にする
4. 候補が選ばれたときだけ、その候補要約を含む入力文を組み立てて共有判断パイプラインへ入る

## 失敗時の扱い

この設計では oldest-first への fallback を入れない。

- eligible 候補が 0 件のときは、selector を呼ばず trigger 既定の挙動へ進む
- selector を呼んだあとで repair 1 回後も parse / contract を満たせなければ、その cycle は `internal_failure` にする
- 選別失敗を silent に `none` 扱いしない
- `selected_candidate_ref=none` は正常系であり failure ではない
- failure event として `pending_intent_selection_failure` を残す

## 観測と監査

inspection で追いやすくするため、`cycle_trace.input_trace` に `pending_intent_selection` を追加する。

最低限、次を持つ。

- `candidate_pool_count`
- `eligible_candidate_count`
- `selected_candidate_ref`
- `selected_candidate_id`
- `selection_reason`
- `result_status`
- `failure_reason`

`selected_candidate_id` は trace 上の内部確認用であり、LLM 入出力には使わない。

`decision_trace.pending_intent_candidate_summary` と `result_trace.pending_intent_summary` は、新しい保留意図を queue へ作成 / 更新した結果を表す。
今回追加する `pending_intent_selection` trace とは責務を混ぜない。

監査 event として `pending_intent_selection_failure` を追加する。
この event は `failure_stage` と `failure_reason` を持ち、cycle 単位で選別失敗を追えるようにする。

## `05_判断と行動.md` との関係

選別は判断本体ではなく、判断へ渡す保留候補を選ぶ前処理である。
最終的に返答、能力実行、保留、見送りのどれを選ぶかは、共有判断パイプラインの `decision_generation` が決める。
