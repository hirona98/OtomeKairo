# API仕様

## API仕様ファミリーの構成

API 仕様は、次のように分ける。

- `design/api/00_API仕様ガイド.md`
  - 共通ルール
  - 認証の基本
  - 共通エラー
- `design/api/01_bootstrapと入力.md`
  - bootstrap
  - 会話入力
  - wake 起床要求
- `design/api/02_event_stream.md`
  - `events/stream`
  - 接続 client の capability binding 提示
- `design/api/03_状態と設定.md`
  - `status`
  - `config`
  - 設定定義の read / replace / delete
- `design/api/04_列挙とinspection.md`
  - `catalog`
  - `inspection`
  - capability availability の確認
  - `logs/stream`
- `design/api/05_実行連携.md`
  - capability 実行要求
  - capability 実行結果
  - capability binding と wire 契約

## 更新ルール

API を実装または変更する場合は、少なくとも次を同じ変更内で更新する。

- 影響を受ける `design/api/` 配下の詳細文書
- API 面の責務境界が変わるなら `11_外部接点とAPI概念.md`
- 接続や権限の意味が変わるなら `12_接続と権限境界.md`
- inspection 面の保証が変わるなら `13_デバッグ可能性.md`
- 判断結果の外向き露出や内部保留の扱いが変わるなら `05_判断と行動.md`
- timestamp 表現が変わるなら `18_時刻モデル.md`
- 現在地、未完了、直近マイルストーンが変わるなら [../plan/01_現行計画.md](../plan/01_現行計画.md)

## 現時点の境界

この API 仕様ファミリーで正本として定めるのは、現行設計の完成形における `design/api/` 配下の path、method、認証、request / response 形式である。
ここには、設計済みだが現行コードへ未反映の endpoint も含む。
実装済み範囲、未実装範囲、直近マイルストーンの現在地は [../plan/01_現行計画.md](../plan/01_現行計画.md) とコードを正とする。

一方で、上位の責務境界は次の文書を正とする。

- `11_外部接点とAPI概念.md`
- `12_接続と権限境界.md`
- `13_デバッグ可能性.md`
- `05_判断と行動.md`
- `17_capability_manifest.md`
- `18_時刻モデル.md`

内部フローや保存先の exact な shape は、この API 仕様ファミリーの対象に含めない。
