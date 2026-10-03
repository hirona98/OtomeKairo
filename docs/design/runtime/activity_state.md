# activity_state

## 目的

`activity_state` は、ユーザーが現在または直前に何をしているかの短期推定を保持する状態である。
判断、発話、自律 initiative では `activity_context` として渡す。

OtomeKairo は、対話入力、API起床要求、観測能力や外部サービス能力の結果を同じ判断ループで扱う。
`activity_state` はその入力群から、ユーザー活動の意味を短期的に推定し、次の判断へ持ち越す。

`activity_state` は desktop capture 専用ではない。
視覚観測、対話、client context、身体、端末、予定、対人文脈、外部サービス結果の短い要約を材料にする。

## 境界

`activity_state` に入れるものは次である。

- ユーザーが現在している活動の推定
- ユーザーが直前までしていた活動の推定
- 活動内容、活動対象、現在活動か直前活動かの短い状態
- 活動主体。`actor_ref` が示す人物の活動として `person` に固定する
- 推定の確からしさ、更新時刻、失効時刻
- 推定に使った source kind と source ref の要約

次は `activity_state` に入れない。

- 生画像、音声、長い payload
- クライアント UI のローカル状態そのもの
- ユーザーの恒久的な習慣や人物理解
- OtomeKairo 自身の実行列
- OtomeKairo の直近発話、約束、待機姿勢をユーザー活動として混ぜたもの
- `speech / noop / pending_intent` の選択、抑制根拠、発話タイミング判断
- capability manifest や binding
- 期限の無い状態

`world_state` は外界条件を保持する。
`activity_state` はユーザー活動の推定を保持する。
`ongoing_action` は OtomeKairo 自身の継続中の能力実行を保持する。
この 3 つを混同しない。
`source_owner=self` のカメラ観測は、構造化された `observed_persons` が活動主体の `actor_ref` と一致する場合に活動推定へ渡す。未同定または別人の視覚説明は本人の活動根拠から分け、世界の視覚前景には保持する。
活動推定層と行動判断層の境界は [../llm/プロンプト文脈分離方針.md](../llm/プロンプト文脈分離方針.md) を正とする。

## 最小構造

1 件の `activity_state` は、少なくとも次を持つ。

| 項目 | 役割 |
|------|------|
| `activity_id` | 状態の識別子 |
| `memory_set_id` | 記憶集合 |
| `label` | 判断へ渡す短い自然文の活動モード要約 |
| `actor_ref` | 活動対象の安定参照。人物状態では `person:*` |
| `actor` | 活動主体の種別。`person` |
| `target` | 活動対象。アプリ名、作品名、相手、作業対象など |
| `status` | 保存内部の生存状態。`active / ended` のいずれか |
| `confidence` | 推定の確からしさ |
| `salience` | 判断前景へ出す強さ |
| `source_kinds` | 推定に使った source kind の配列 |
| `source_refs` | 根拠となる `cycle_id`、`request_id` など |
| `started_at` | 活動が始まったと推定した時刻 |
| `updated_at` | 最終更新時刻 |
| `expires_at` | 状態の失効時刻 |
| `transition` | 直前活動に対する `start / continue / switch / end / none` の推定 |
| `previous_activity` | 直前活動の短い要約 |

`expires_at` は必須とする。
`activity_state` は短期推定であり、期限の無い状態を作らない。

## 推定責務

activity 推定は LLM 補助契約で行う。
コードは時系列、source、TTL、置換、終了、検証、保存を管理する。

LLM には、少なくとも次の要約を source pack として渡す。

- `current_input`
- `activity_subject`（コードが確定した `actor=person` と `actor_ref`）
- `recent_turns`
- `time_context`
- `client_context`
- `observation_summary`
- `visual_observation_context`
- `foreground_world_state`
- `previous_activity_context`
- `source_owner`

入力がない field は渡さない。
raw image、音声、長い payload、資格情報、内部 URL、配送先 client は渡さない。

LLM は文字列一致で活動を確定しない。
LLM は複数 source の意味を見て、活動候補を返す。
コード側もアプリ名やタイトルの文字列一致で活動内容を決めない。
文字列比較は同一活動の統合、重複抑制、inspection の補助に限定する。
`desktop / virtual` の vision source と `source_owner=user_environment` は人物側の環境観測として扱い、人物文脈が確定している場合だけ activity candidate の `actor=person` にする。
`source_owner=self` の camera 観測は OtomeKairo の視覚根拠として扱い、観測対象の人物参照が確定している場合だけ `actor=person` の activity candidate に使う。
コードは source pack の `observed_person_refs` と `activity_subject.actor_ref` の一致を検証する。未同定のカメラ人物についての `observation` 候補は会話話者の活動として採用せず、本人の `activity_report` は別に採用できる。
activity の `label / reason_summary` はユーザー側の観測事実から構成する。
assistant の直近発話、約束、待機姿勢は activity とは別文脈として扱う。
activity の `label` は具体的な内容名や対象名ではなく、判断と発話でそのまま使える短い活動モードにする。
内容名、対象名、作業対象などの詳細は `target / reason_summary` に置く。

### 活動内容の表現

活動内容は enum にしない。
活動内容は `label` の自然文で表す。
`label` は判断と発話でそのまま使える短い自然文にする。

`target` は活動対象が自然に分かる場合だけ入れる。
対象が不明な場合は空文字にする。

活動内容を分類語だけにしない。
分類名だけの `label` は使わない。
根拠不足で活動内容を自然文にできない場合、LLM は候補を返さない。

## LLM 出力契約

LLM の出力は JSON object 1 個に固定する。

```json
{
  "activity_candidates": [
    {
      "actor": "person",
      "label": "活動モードを短く表す自然文",
      "target": "活動対象を短く表す文字列",
      "confidence_hint": "high",
      "salience_hint": "high",
      "ttl_hint": "short",
      "transition": "continue",
      "reason_summary": "観測事実から活動モードを判断した根拠"
    }
  ]
}
```

契約は次とする。

- 必須トップレベルキーは `activity_candidates` だけにする
- `activity_candidates` は最大 1 件の配列にする
- 候補がない場合は空配列にする
- 各候補は `actor / label / target / confidence_hint / salience_hint / ttl_hint / transition / reason_summary` だけを持つ
- `actor` は `person` にする。主体が確定しない場合は候補を返さない
- `label` は活動内容を自然文で短く表す
- `confidence_hint`、`salience_hint` は `low / medium / high` のいずれかにする
- `ttl_hint` は `short / medium / long` のいずれかにする
- `transition` は `start / continue / switch / end / none` のいずれかにする
- `label`、`target`、`reason_summary` は短くし、内部識別子を含めない

## 更新規則

候補が非空の場合は、独立した `activity_state_grounding_review` に source pack と候補を渡す。人物についての根拠がある候補を採用し、現在の個の返答準備などを人物へ移した候補は除く。審査は [状態候補根拠審査](../llm/状態候補根拠審査.md) の共通契約を使う。審査に失敗した場合は更新を失敗させる。

コードは LLM 出力を受けて次を決める。

- `activity_id`
- 数値 `confidence / salience`
- `source_kinds / source_refs`
- `started_at / updated_at / expires_at`
- 保存内部の `status`
- 既存 activity との継続、切替、終了
- `previous_activity`

`transition=continue` では既存 activity を継続更新し、`previous_activity` は保持する。継続中の current を終了済み previous に移さない。
`transition=start` または `switch` では、既存 activity を `previous_activity` に移し、新しい activity を current にする。
`transition=end` では、既存 activity を `previous_activity` に移し、current を空にする。
`transition=start` は初めて把握した現在活動の登録を表し、その活動の物理的な開始時刻が報告されたことを要件としない。`continue` は同じ活動の更新、`switch` は別の活動への切り替え、`end` は活動の終了を表す。
`transition=none` または候補なしでは、既存 activity を保存したまま、期限切れだけを処理する。
保存内部では、current activity を `active`、終了済み activity を `ended` として扱う。
current activity は `memory_set_id / actor_ref` ごとに1件を持つ。
人物参照が無い定期思考は人物の activity state を読み書きしない。
LLM は `status` を出力しない。

現在入力の `source_kind=user_message` で、直前 activity が短時間以内に存在する場合、`previous_activity` を判断文脈へ出す。
これは現在入力だけでは参照先が曖昧な発話を、直前活動の文脈で解釈するために使う。

## 判断入力

判断、発話、自律 initiative へ渡す `activity_context` は、保存 row ではなく前景要約にする。
`previous_activity` は直前活動だけを表し、現在進行中の活動として扱わない。
`last_known_activity` はその人物について最後に把握した推定を表し、期限切れ・終了後も保持済みrowから取得する。
`current_activity` へ過去の状態を代入せず、現在有効な推定と最後の把握記録を別に渡す。
`last_known_activity` は人物参照、根拠の経過、説明、推定という根拠種類を保持する。現在まで続いた活動区間を表す `duration_label` は含めない。
判断文脈へ出す `activity_context` には `status` を含めない。
判断文脈へ出す `activity_context.current_activity.actor` は speech の主体境界に使う。
`current_activity / previous_activity / last_known_activity` の `actor_ref` は、活動の人物との対応を保つため圧縮表現にも残す。
`actor=person` の活動に触れる発話は、`actor_ref` の人物側の状況へのコメントとして表現する。
判断文脈へ出す `activity_context.current_activity` には、活動推定 LLM が返した `transition` を含める。
判断文脈へ出す `activity_context.current_activity / previous_activity` には、時刻そのものではなく `started_age_label / duration_label / ended_age_label` のような生活文脈向けラベルを含める。
これにより、長く続いた直前活動が `直前` という終了時点だけへ圧縮されないようにする。
`current_activity.age_label` は最後に活動を支えた根拠からの経過を表す。`started_age_label / duration_label` は推定上の活動区間であり、その間の継続を観測し続けた実績ではない。
有効期限内の状態保持と、回答時点の継続確認は分ける。判断、発話生成、発話の根拠審査では、現在の本人報告または本人と同定された新しい観測で現在の継続を確認する。以前の報告だけが根拠なら、最後に聞いた活動として述べ、現在も続いているかは未確認として扱う。
最後に把握した活動を尋ねられた場合は、その人物の `activity_context` を根拠にする。現在の相互作用の直近会話に元の報告がなくても、保持済みの活動と現在の継続の確認不足を分けて答える。

```json
{
  "current_activity": {
    "actor": "person",
    "actor_ref": "person:external-123",
    "label": "現在活動を短く表す自然文",
    "transition": "switch",
    "confidence": 0.7,
    "salience": 0.5,
    "started_age_label": "直前",
    "duration_label": "1分未満",
    "age_label": "直前"
  },
  "previous_activity": {
    "actor": "person",
    "actor_ref": "person:external-123",
    "label": "直前活動を短く表す自然文",
    "target": "直前活動の対象",
    "started_age_label": "6時間前",
    "duration_label": "約6時間",
    "ended_age_label": "直前",
    "confidence": 0.82
  }
}
```

`activity_context` はユーザー発話ではない。
`current_input.sender_kind=person` の本文と混ぜない。
自律 initiative では、`activity_context` をタイミング判断の補助材料として扱う。
コードは `label / target` の語句一致で活動分類を固定しない。
コードは `activity_context` だけを理由に `suppression_level` を上げない。
コードは `activity_context` だけを理由に `speech / noop / pending_intent` を固定しない。

## 記憶との関係

`activity_state` は短期推定である。
長期記憶へ入れる場合は、turn consolidation が通常の記憶更新規則で判断する。

`activity_state.label` をそのまま `memory_units.summary_text` に複写しない。
ユーザーが明示した活動、観測に基づく出来事、会話上意味のある流れだけを、events / episodes を根拠に候補化する。

## inspection

inspection と cycle trace では、少なくとも次を追えるようにする。

- activity 推定を実行したか
- source pack の要約
- LLM 候補件数
- current activity の要約
- previous activity の要約
- 更新、切替、終了、失効の結果
- 失敗理由

通常の `GET /api/status` には、activity row の生 payload を返さない。

## やらないこと

次は採らない。

- desktop capture 専用の仕組みにすること
- active app や window title の文字列一致で活動内容を決めること
- `recent_turns` に system 観測を混ぜること
- `world_state` にユーザー活動推定を押し込むこと
- `activity_state` を長期記憶の代替にすること
- TTL の無い活動状態を作ること
