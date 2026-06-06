# activity_state

## 目的

`activity_state` は、ユーザーが現在または直前に何をしているかの短期推定を保持する状態である。
判断、発話、自律 initiative では `activity_context` として渡す。

OtomeKairo は、対話入力、API起床要求、観測能力の結果、外部サービスや各種 status capability の結果を同じ判断ループで扱う。
`activity_state` はその入力群から、ユーザー活動の意味を短期的に推定し、次の判断へ持ち越す。

`activity_state` は desktop capture 専用ではない。
視覚観測、対話、client context、身体、端末、予定、対人文脈、外部サービス結果の短い要約を材料にする。

## 境界

`activity_state` に入れるものは次である。

- ユーザーが現在している活動の推定
- ユーザーが直前までしていた活動の推定
- 活動内容、活動対象、現在活動か直前活動かの短い状態
- 活動主体。通常は `user`、AI 本体の ongoing action と構造的に分かる場合だけ `self`
- 推定の確からしさ、更新時刻、失効時刻
- 推定に使った source kind と source ref の要約

次は `activity_state` に入れない。

- 生画像、音声、長い payload
- クライアント UI のローカル状態そのもの
- ユーザーの恒久的な習慣や人物理解
- OtomeKairo 自身の実行列
- OtomeKairo の直近発話、約束、待機姿勢をユーザー活動として混ぜたもの
- capability manifest や binding
- 期限の無い状態

`world_state` は外界条件を保持する。
`activity_state` はユーザー活動の推定を保持する。
`ongoing_action` は OtomeKairo 自身の継続中の能力実行を保持する。
この 3 つを混同しない。

## 最小構造

1 件の `activity_state` は、少なくとも次を持つ。

| 項目 | 役割 |
|------|------|
| `activity_id` | 状態の識別子 |
| `memory_set_id` | 記憶集合 |
| `label` | 判断へ渡す短い自然文の活動モード要約 |
| `actor` | 活動主体。`user / self / unknown` のいずれか |
| `target` | 活動対象。アプリ名、作品名、相手、作業対象など |
| `status` | 保存内部の生存状態。`active / ended` のいずれか |
| `confidence` | 推定の確からしさ |
| `salience` | 判断前景へ出す強さ |
| `source_kinds` | 推定に使った source kind の配列 |
| `source_refs` | 根拠となる `cycle_id`、`request_id` など |
| `started_at` | 活動が始まったと推定した時刻 |
| `updated_at` | 最終更新時刻 |
| `expires_at` | 状態の失効時刻 |
| `previous_activity` | 直前活動の短い要約 |

`expires_at` は必須とする。
`activity_state` は短期推定であり、期限の無い状態を作らない。

## 推定責務

activity 推定は LLM 補助契約で行う。
コードは時系列、source、TTL、置換、終了、検証、保存を管理する。

LLM には、少なくとも次の要約を source pack として渡す。

- `current_input`
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
`desktop / virtual` の vision source と `source_owner=user_environment` はユーザー側の環境観測として扱い、activity candidate の `actor=user` にする。
`source_owner=self` の camera 観測は OtomeKairo の視覚根拠として扱い、観測対象がユーザー活動だと判断できる場合だけ `actor=user` の activity candidate に使う。
activity の `label / reason_summary` はユーザー側の観測事実から構成する。
assistant の直近発話、約束、待機姿勢は activity とは別文脈として扱う。
activity の `label` は投稿内容、検索語、曲名、ファイル名などの細部ではなく、X閲覧中、検索で調査中、コーディング中、ゲーム中、音楽鑑賞中のような活動モードにする。
作品名、曲名、投稿内容、作業対象などの詳細は `target / reason_summary` に置く。

### 活動内容の表現

活動内容は enum にしない。
活動内容は `label` の自然文で表す。
`label` は「リズムゲームをプレイ中」「資料を読みながら作業中」「休憩している」のように、判断と発話でそのまま使える短い表現にする。

`target` は活動対象が自然に分かる場合だけ入れる。
対象が不明な場合は空文字にする。

活動内容を分類語だけにしない。
「gameplay」「work」「media」のような分類名だけの `label` は使わない。
根拠不足で活動内容を自然文にできない場合、LLM は候補を返さない。

## LLM 出力契約

LLM の出力は JSON object 1 個に固定する。

```json
{
  "activity_candidates": [
    {
      "actor": "user",
      "label": "リズムゲームをプレイ中",
      "target": "KAMITSUBAKI CITY ENSEMBLE",
      "confidence_hint": "high",
      "salience_hint": "high",
      "ttl_hint": "short",
      "transition": "continue",
      "reason_summary": "視覚観測でゲームプレイ画面が継続している。"
    }
  ]
}
```

契約は次とする。

- 必須トップレベルキーは `activity_candidates` だけにする
- `activity_candidates` は最大 1 件の配列にする
- 候補がない場合は空配列にする
- 各候補は `actor / label / target / confidence_hint / salience_hint / ttl_hint / transition / reason_summary` だけを持つ
- `actor` は `user / self / unknown` のいずれかにする
- `label` は活動内容を自然文で短く表す
- `confidence_hint`、`salience_hint` は `low / medium / high` のいずれかにする
- `ttl_hint` は `short / medium / long` のいずれかにする
- `transition` は `start / continue / switch / end / none` のいずれかにする
- `label`、`target`、`reason_summary` は短くし、内部識別子を含めない

## 更新規則

コードは LLM 出力を受けて次を決める。

- `activity_id`
- 数値 `confidence / salience`
- `source_kinds / source_refs`
- `started_at / updated_at / expires_at`
- 保存内部の `status`
- 既存 activity との継続、切替、終了
- `previous_activity`

`transition=continue` では既存 activity を継続更新する。
`transition=start` または `switch` では、既存 activity を `previous_activity` に移し、新しい activity を current にする。
`transition=end` では、既存 activity を `previous_activity` に移し、current を空にする。
`transition=none` または候補なしでは、既存 activity を保存したまま、期限切れだけを処理する。
保存内部では、current activity を `active`、終了済み activity を `ended` として扱う。
LLM は `status` を出力しない。

現在入力が user message で、直前 activity が短時間以内に存在する場合、`previous_activity` を判断文脈へ出す。
これは「今はチャット画面に戻っているが、直前までゲームをしていた」のような発話解釈に使う。

## 判断入力

判断、発話、自律 initiative へ渡す `activity_context` は、保存 row ではなく前景要約にする。
`previous_activity` は直前活動だけを表し、現在進行中の活動として扱わない。
判断文脈へ出す `activity_context` には `status` を含めない。
判断文脈へ出す `activity_context.current_activity.actor` は speech の主体境界に使う。
`actor=user` の活動に触れる発話は、ユーザー側の状況へのコメントとして表現する。

```json
{
  "current_activity": {
    "actor": "user",
    "label": "CocoroAI で会話中",
    "confidence": 0.7,
    "salience": 0.5,
    "age_label": "直前"
  },
  "previous_activity": {
    "actor": "user",
    "label": "リズムゲームをプレイしていた",
    "target": "KAMITSUBAKI CITY ENSEMBLE",
    "ended_age_label": "直前",
    "confidence": 0.82
  }
}
```

`activity_context` はユーザー発話ではない。
`current_input.sender=user` の本文と混ぜない。
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
