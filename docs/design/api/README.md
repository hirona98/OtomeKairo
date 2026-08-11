# API 仕様

## API仕様ファミリーの構成

このフォルダは HTTP / WebSocket API の wire 契約を正本にする。
path、method、認証、request / response、error code はこのフォルダで定める。
意味境界、状態遷移、capability、記憶、LLM role の規則は対応する design 文書を正とする。

| 文書 | 内容 |
| --- | --- |
| [共通ルール.md](共通ルール.md) | 共通ルール、認証、共通エラー |
| [bootstrapと入力.md](bootstrapと入力.md) | bootstrap、会話入力、`wake` |
| [event_stream.md](event_stream.md) | `events/stream`、capability binding 提示 |
| [audio_stream.md](audio_stream.md) | 音声 PCM stream、STT/TTS トグル、話者管理 |
| [状態と設定.md](状態と設定.md) | `status`、`config`、設定定義の read / replace / delete |
| [列挙とinspection.md](列挙とinspection.md) | `catalog`、`docs`、`inspection`、`logs/stream` |
| [実行連携.md](実行連携.md) | capability 実行要求・結果・state 操作 |

## ブラウザ UI 配信面

`GET /ui/` とその静的 asset は同一 HTTPS server から配信するブラウザ UI である。
`/ui/` は API wire 契約の正本ではなく、既存 `/api/...` endpoint を呼び出す client 実装として扱う。
`GET /` は `/ui/` へリダイレクトする。
`/ui/api/...` はブラウザ UI 専用の同一 server 内部呼び出し面であり、外部接点向け API として扱わない。
`/ui/api/...` は server が保持する `console_access_token` で認可し、token をブラウザへ返さない。
WebSocket 系の `/ui/api/...` は `Origin` と `Host` が一致する同一 origin の接続だけを受理する。
`/ui/` と `/ui/api/...` の追加は `/api/...` の path、method、認証、request / response 形式を変更しない。

見た目規約は次を正とする。

- 設定パネル: [../integration/WebUI設定フォーム規約.md](../integration/WebUI設定フォーム規約.md)
- 通常画面: [../integration/WebUI通常画面規約.md](../integration/WebUI通常画面規約.md)
- 確認系 UI（いま / 判断 / ログ）: [../runtime/デバッグ可能性.md](../runtime/デバッグ可能性.md)

## 更新ルール

API を実装または変更する場合は、少なくとも次を同じ変更内で更新する。

- 影響を受けるこのフォルダの詳細文書
- API 面の責務境界が変わるなら [../integration/外部接点とAPI概念.md](../integration/外部接点とAPI概念.md)
- 接続や権限の意味が変わるなら [../integration/接続と権限境界.md](../integration/接続と権限境界.md)
- inspection 面の保証が変わるなら [../runtime/デバッグ可能性.md](../runtime/デバッグ可能性.md)
- 判断結果の外向き露出や内部保留の扱いが変わるなら [../runtime/判断と行動.md](../runtime/判断と行動.md)
- timestamp 表現が変わるなら [../runtime/時刻モデル.md](../runtime/時刻モデル.md)
- 実装確認手順が変わるなら [../../../README.md](../../../README.md) または `scripts/run_long_smoke.py`

## 境界

この API 仕様ファミリーで正本として定めるのは、`docs/design/api/` 配下の path、method、認証、request / response 形式である。

上位の責務境界は次の文書を正とする。

- [../integration/外部接点とAPI概念.md](../integration/外部接点とAPI概念.md)
- [../integration/接続と権限境界.md](../integration/接続と権限境界.md)
- [../runtime/デバッグ可能性.md](../runtime/デバッグ可能性.md)
- [../runtime/判断と行動.md](../runtime/判断と行動.md)
- [../capability/capability_manifest.md](../capability/capability_manifest.md)
- [../runtime/時刻モデル.md](../runtime/時刻モデル.md)

内部フローや保存先の exact な shape は、この API 仕様ファミリーの対象に含めない。
