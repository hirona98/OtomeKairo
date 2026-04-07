# API仕様ガイド

<!-- Role -->
## この文書の役割

この文書は、`design/api/` 配下に共通する wire ルールを定める土台である。

ここで固定するのは次である。

- 共通ルール
- 認証の基本
- 共通エラー

各 endpoint の詳細は、この配下の個別文書で定める。

<!-- Scope -->
## 適用範囲

API 仕様ファミリー全体の入口と分割方針は `../14_API仕様.md` を正とする。
この文書では、その配下にある詳細文書へ共通して適用するルールだけを扱う。

API を変更するときは、該当する詳細文書に加えて `../14_API仕様.md` の見取り図と、この文書の共通ルールがずれていないかを同じ変更内で確認する。

<!-- Base -->
## 共通ルール

### ベース

- ベース URL は `https://<host>:<port>` とする
- request / response は `application/json` とする
- 成功時は常に `{"ok": true, "data": ...}` を返す
- 失敗時は常に `{"ok": false, "error": {"code": "...", "message": "..."}}` を返す

ただし `GET /api/events/stream` と `GET /api/logs/stream` は WebSocket upgrade endpoint として扱い、この共通 envelope から外してよい。

### 認証

- `GET /api/bootstrap/probe`
- `GET /api/bootstrap/server-identity`
- `POST /api/bootstrap/register-first-console`

上の 3 つは未認証で呼べてよい。

それ以外の API は `Authorization: Bearer <console_access_token>` を必須とする。

<!-- CommonErrors -->
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
