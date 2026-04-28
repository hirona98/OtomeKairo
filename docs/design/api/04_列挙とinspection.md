# 列挙とinspection

## この文書の境界

この文書は、列挙 API と inspection API の path、method、認証、request / response、error code を正本にする。
capability availability の意味規則は [../17_capability_manifest.md](../17_capability_manifest.md)、段階トレースの意味規則は [../13_デバッグ可能性.md](../13_デバッグ可能性.md) を正とする。
この文書では、各 endpoint の top-level field と wire 上の enum だけを定める。

## 列挙面

### `GET /api/catalog`

- 認証: 必要
- 役割: 人格設定、記憶集合、モデルプリセットの一覧を返す
- capability manifest 一覧と capability availability は返さない
- capability manifest と capability availability の境界は [../17_capability_manifest.md](../17_capability_manifest.md) を正とする

response:

```json
{
  "ok": true,
  "data": {
    "personas": [
      {
        "persona_id": "persona:default",
        "display_name": "標準人格設定"
      }
    ],
    "memory_sets": [
      {
        "memory_set_id": "memory_set:default",
        "display_name": "Default Memory"
      }
    ],
    "model_presets": [
      {
        "model_preset_id": "model_preset:default",
        "display_name": "Default OpenRouter Gemini Preset"
      }
    ]
  }
}
```

## inspection 面

### `GET /api/inspection/capabilities`

- 認証: 必要
- 役割: server が manifest、binding、`capability_state`、権限から導出した現在の capability availability を返す
- この response は capability availability の外向き確認正本である
- この endpoint は現行設計の完成形における wire 契約である。実装状態は `../../plan/` 配下を正とする
- `capabilities` は server が知っている manifest を基準に並べる
- `rejected_bindings` は、接続 client が `hello.caps` で提示したが server が binding として受理しなかった候補を返す
- token、credential、内部 URL、transport 詳細は返さない
- availability 判定の入力と decision view との関係は [../17_capability_manifest.md](../17_capability_manifest.md) を正とする

response:

```json
{
  "ok": true,
  "data": {
    "generated_at": "2026-03-31T09:00:00+09:00",
    "capabilities": [
      {
        "capability_id": "vision.capture",
        "manifest_version": "1",
        "kind": "observation",
        "available": true,
        "unavailable_reason": null,
        "binding": {
          "status": "bound",
          "eligible_client_count": 1,
          "bound_client_ids": ["console-..."]
        },
        "permissions": {
          "required": ["observe_desktop"],
          "missing": []
        },
        "state": {
          "paused": false,
          "cooldown_until": null,
          "last_failure_at": null,
          "last_failure_summary": null,
          "parallel_blocked_by_action_id": null
        }
      }
    ],
    "rejected_bindings": [
      {
        "client_id": "console-...",
        "capability_id": "vision.capture",
        "offered_version": "0",
        "rejection_reason": "unsupported_version",
        "seen_at": "2026-03-31T09:00:00+09:00"
      }
    ]
  }
}
```

`unavailable_reason` は `available=true` のとき `null` である。
`available=false` のとき、`unavailable_reason` は次のいずれかである。

| 値 | 意味 |
|----|------|
| `no_binding` | 実行できる接続 client がない |
| `permission_denied` | 必要権限を満たす接続主体がない |
| `paused` | server 側で一時停止している |
| `cooldown` | cooldown 中である |
| `precondition_failed` | 実行前提を満たしていない |
| `recent_failure` | 直近失敗により一時的に実行不可である |
| `parallel_blocked` | 並列実行制限により実行不可である |
| `transport_unavailable` | stream などの配送経路が利用できない |

`binding.status` は次のいずれかである。

| 値 | 意味 |
|----|------|
| `bound` | 実行候補として受理済みの接続 client がある |
| `no_binding` | capability を実行できる接続 client がない |
| `rejected_only` | 提示された候補はあるが、すべて拒否された |

`rejected_bindings.rejection_reason` は次のいずれかである。

| 値 | 意味 |
|----|------|
| `unknown_capability` | server が知らない capability id である |
| `unsupported_version` | server が対応しない manifest version である |
| `permission_denied` | 接続主体が必要権限を満たさない |

### `GET /api/inspection/cycle-summaries?limit=<n>`

- 認証: 必要
- 役割: 最近の `cycle_summary` 一覧を返す
- `limit` は省略時 `20`
- `started_at` / `finished_at` は OtomeKairo のローカルタイムゾーンに属する offset 付き timestamp で返す

response:

```json
{
  "ok": true,
  "data": {
    "cycle_summaries": [
      {
        "cycle_id": "cycle:...",
        "server_id": "server:...",
        "trigger_kind": "user_message",
        "started_at": "2026-03-31T09:00:00+09:00",
        "finished_at": "2026-03-31T09:00:00+09:00",
        "result_kind": "reply",
        "failed": false
      }
    ]
  }
}
```

### `GET /api/inspection/cycles/{cycle_id}`

- 認証: 必要
- 役割: 指定した `cycle_id` の段階トレースを返す
- 含まれる timestamp 系フィールドは OtomeKairo のローカルタイムゾーンに属する offset 付き timestamp で返す

response:

```json
{
  "ok": true,
  "data": {
    "cycle_id": "cycle:...",
    "cycle_summary": {},
    "input_trace": {},
    "world_state_trace": {},
    "recall_trace": {},
    "decision_trace": {},
    "result_trace": {},
    "memory_trace": {}
  }
}
```

top-level の trace object は、存在しない段階でも空 object として返す。
各 trace object の意味と標準的な含有内容は [../13_デバッグ可能性.md](../13_デバッグ可能性.md) を正とする。
機能ごとの追加 field は、それぞれの設計文書を正とする。

| trace | 詳細正本 |
|-------|----------|
| `input_trace` | [../13_デバッグ可能性.md](../13_デバッグ可能性.md)、[../21_自律initiative_loop.md](../21_自律initiative_loop.md)、[../15_保留意図候補のLLM選別.md](../15_保留意図候補のLLM選別.md) |
| `world_state_trace` | [../22_world_state.md](../22_world_state.md) |
| `recall_trace` | [../memory/03_想起と判断.md](../memory/03_想起と判断.md)、[../memory/08_event_evidenceのLLM圧縮.md](../memory/08_event_evidenceのLLM圧縮.md)、[../memory/09_RecallPackのLLM選別.md](../memory/09_RecallPackのLLM選別.md) |
| `decision_trace` | [../05_判断と行動.md](../05_判断と行動.md)、[../21_自律initiative_loop.md](../21_自律initiative_loop.md) |
| `result_trace` | [../05_判断と行動.md](../05_判断と行動.md)、[../17_capability_manifest.md](../17_capability_manifest.md) |
| `memory_trace` | [../13_デバッグ可能性.md](../13_デバッグ可能性.md)、[../memory/04_記憶更新と再整理.md](../memory/04_記憶更新と再整理.md)、[../memory/07_内省要約のLLM生成.md](../memory/07_内省要約のLLM生成.md) |

主な失敗:

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `404` | `cycle_not_found` | 指定した `cycle_id` が存在しない |

### `GET /api/logs/stream`

- 認証: 必要
- 役割: `CocoroConsole` のログビューアー向けに、判断サイクルの短い段階要約ログを WebSocket で流す
- client から送る message は不要
- 接続時には、直近の短いログを replay する
- `ts` は OtomeKairo のローカルタイムゾーンに属する offset 付き timestamp で返す

message shape:

```json
[
  {
    "ts": "2026-04-06T09:00:00+09:00",
    "level": "INFO",
    "logger": "RecallStructured",
    "msg": "cf09b49a3ce1 memory_units=memory_unit:1234abcd episodes=episode:5678efgh"
  }
]
```

`logger` には少なくとも次を流す。

- `Observation`
- `RecallHint`
- `RecallStructured`
- `RecallAssociation`
- `RecallResult`
- `Decision`
- `Result`
- `Memory`

ここで流すのは live 表示向けの派生ログであり、inspection の正本ではない。
完全な prompt、生の LLM 応答全文、長い思考過程は流さない。
