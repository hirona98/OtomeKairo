# API仕様

## API仕様ファミリーの構成

API 仕様は、次のように分ける。

- `design/api/00_API仕様ガイド.md`
  - 共通ルール
  - 認証の基本
  - 共通エラー
- `design/api/01_bootstrapと観測.md`
  - bootstrap
  - 会話観測
  - wake 観測
- `design/api/02_event_stream.md`
  - `events/stream`
  - `vision/capture-response`
- `design/api/03_状態と設定.md`
  - `status`
  - `config`
  - 設定資源の read / replace / delete
- `design/api/04_列挙とinspection.md`
  - `catalog`
  - `inspection`
  - `logs/stream`

## 更新ルール

API を実装または変更する場合は、少なくとも次を同じ変更内で更新する。

- 影響を受ける `design/api/` 配下の詳細文書
- API 面の責務境界が変わるなら `11_外部接点とAPI概念.md`
- 接続や権限の意味が変わるなら `12_接続と権限境界.md`
- inspection 面の保証が変わるなら `13_デバッグ可能性.md`
- 判断結果の外向き露出や内部保留の扱いが変わるなら `05_判断と行動.md`
- 現在地や未完了が変わるなら `plan/` 配下の実装計画文書

## 現時点の境界

この API 仕様ファミリーで正本として定めるのは、`design/api/` 配下にある path、method、認証、request / response 形式である。

一方で、上位の責務境界は次の文書を正とする。

- `11_外部接点とAPI概念.md`
- `12_接続と権限境界.md`
- `13_デバッグ可能性.md`
- `05_判断と行動.md`

内部フローや保存先の exact な shape は、この API 仕様ファミリーの対象に含めない。
