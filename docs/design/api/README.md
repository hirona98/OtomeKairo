# API 仕様

## API仕様ファミリーの構成

このフォルダは HTTP / WebSocket API の wire 契約を正本にする。
path、method、認証、request / response、error code はこのフォルダで定める。
意味境界、状態遷移、capability、記憶、LLM role の規則は対応する design 文書を正とする。

API 仕様は次のように分ける。

- [共通ルール.md](共通ルール.md)
  - 共通ルール
  - 認証の基本
  - 共通エラー
- [bootstrapと入力.md](bootstrapと入力.md)
  - bootstrap
  - 会話入力
  - `wake` API起床要求
- [event_stream.md](event_stream.md)
  - `events/stream`
  - 接続 client の capability binding 提示
- [状態と設定.md](状態と設定.md)
  - `status`
  - `config`
  - 設定定義の read / replace / delete
- [列挙とinspection.md](列挙とinspection.md)
  - `catalog`
  - `docs`
  - `inspection`
  - capability availability の確認
  - `logs/stream`
- [実行連携.md](実行連携.md)
  - capability 実行要求
  - capability 実行結果
  - capability binding と HTTP / WebSocket 通信仕様
  - capability state 操作

## ブラウザUI配信面

`GET /ui/` とその静的 asset は、同一 HTTPS server から配信するブラウザ UI である。
`/ui/` は API wire 契約の正本ではなく、既存 `/api/...` endpoint を呼び出す client 実装として扱う。
`GET /` は `/ui/` へリダイレクトする。
`/ui/api/...` はブラウザ UI 専用の同一 server 内部呼び出し面であり、外部接点向け API として扱わない。
ブラウザ UI は `/ui/api/conversation` を通じて既存の会話入力処理を呼び出す。
ブラウザ UI は永続化した `person_ref / display_name / interaction_ref` を `interaction_context` として送る。
ブラウザ UI は CocoroConsole と同じ責務カテゴリの左ナビゲーションを持ち、`/ui/api/config/...` を通じて既存設定操作を呼び出す。
ブラウザ UI は `/ui/api/config/avatar-speech/editor-state` を通じて、アバターごとの STT / TTS とマイクしきい値を編集する。
話者登録 UI は表示だけを行い、音声処理を OtomeKairo へ実装するまで操作を無効にする。
現段階のブラウザ UI は音声を取得、upload、再生せず、STT / TTS を実行しない。
将来のブラウザは音声の取得・送信と受信・再生だけを担当し、STT / TTS を OtomeKairo が担当する。
ブラウザ UI は運用ダッシュボードを常設し、`/ui/api/inspection/current-state` と `/ui/api/inspection/cycle-summaries` を 5 秒周期で読み取る。
運用ダッシュボードは現在状態、自律実行、capability availability、最近の cycle timeline を表示する。
運用ダッシュボードの pause / resume / cancel は `/ui/api/autonomous-runs/{run_id}/{operation}` を通じて既存の autonomous run 操作を呼び出す。
ブラウザ UI は対話入力と同じ session-scoped `client_id` で `/ui/api/events/stream` へ接続し、`assistant_message` を会話欄へ即時表示する。
`/ui/api/events/stream` は server が保持する `console_access_token` で認可し、token をブラウザへ返さない。
`/ui/api/events/stream` は `Origin` と `Host` が一致する同一 origin の接続だけを受理する。
ブラウザ UI は画面上で `console_access_token` の入力を要求しない。
`/ui/` と `/ui/api/...` の追加は `/api/...` の path、method、認証、request / response 形式を変更しない。

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

一方で、上位の責務境界は次の文書を正とする。

- [../integration/外部接点とAPI概念.md](../integration/外部接点とAPI概念.md)
- [../integration/接続と権限境界.md](../integration/接続と権限境界.md)
- [../runtime/デバッグ可能性.md](../runtime/デバッグ可能性.md)
- [../runtime/判断と行動.md](../runtime/判断と行動.md)
- [../capability/capability_manifest.md](../capability/capability_manifest.md)
- [../runtime/時刻モデル.md](../runtime/時刻モデル.md)

内部フローや保存先の exact な shape は、この API 仕様ファミリーの対象に含めない。
