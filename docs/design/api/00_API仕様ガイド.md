# API仕様ガイド

<!-- Block: Role -->
## この文書の役割

この文書は、`design/api/` 配下の API 仕様をどう読むかを示す入口である。

ここで固定するのは次である。

- API 仕様ファミリーの分割方針
- 共通ルール
- 認証の基本
- 共通エラー

各 endpoint の詳細は、この配下の個別文書で定める。

<!-- Block: Scope -->
## API仕様ファミリーの構成

API 仕様は、次の 4 本に分ける。

- `01_bootstrapと観測.md`
  - bootstrap
  - 会話観測
  - wake 観測
- `02_event_stream.md`
  - `events/stream`
  - `vision/capture-response`
- `03_状態と設定.md`
  - `status`
  - `config`
  - 設定資源の read / replace / delete
- `04_列挙とinspection.md`
  - `catalog`
  - `inspection`

API を変更するときは、該当する詳細文書と、このガイドの分割方針がずれていないかを同じ変更内で確認する。

<!-- Block: Base -->
## 共通ルール

### ベース

- ベース URL は `https://<host>:<port>` とする
- request / response は `application/json` とする
- 成功時は常に `{"ok": true, "data": ...}` を返す
- 失敗時は常に `{"ok": false, "error": {"code": "...", "message": "..."}}` を返す

ただし `GET /api/events/stream` は WebSocket upgrade endpoint として扱い、この共通 envelope から外してよい。

### 認証

- `GET /api/bootstrap/probe`
- `GET /api/bootstrap/server-identity`
- `POST /api/bootstrap/register-first-console`

上の 3 つは未認証で呼べてよい。

それ以外の API は `Authorization: Bearer <console_access_token>` を必須とする。

<!-- Block: CommonErrors -->
## 共通エラー

| HTTP | `error.code` | 意味 |
|------|--------------|------|
| `400` | `invalid_json` | JSON として解釈できない |
| `400` | `invalid_json_shape` | request body が object ではない |
| `401` | `bootstrap_required` | まだ `console_access_token` が発行されていない |
| `401` | `invalid_token` | 認証トークンが無い、または不正 |
| `404` | `route_not_found` | 未定義の route |
| `409` | `last_resource_delete_forbidden` | 最後の 1 件を削除しようとした |
| `500` | `internal_server_error` | サーバ内部で失敗した |
