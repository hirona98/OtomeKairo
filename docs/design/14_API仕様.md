# API仕様

<!-- Role -->
## この文書の役割

この文書は、OtomeKairo の API 仕様ファミリー全体の入口である。

API の厳密な正本は、この文書単体ではなく、`design/api/` 配下の各文書を含めた一式とする。

ここで固定するのは次である。

- API 仕様の分割単位
- どの詳細をどこで定めるか
- API を変更したときに更新すべき文書群

<!-- ParentDocs -->
## 上位文書

この API 仕様ファミリーは、少なくとも次の上位文書を具体化する。

- `08_API概念.md`
- `11_接続と認証.md`
- `12_デバッグ可能性.md`
- `13_自発判断と自発行動.md`

ここでは、それらの上位文書で定めた責務境界を変えない。

<!-- Layout -->
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

<!-- UpdateRule -->
## 更新ルール

API を実装または変更する場合は、少なくとも次を同じ変更内で更新する。

- 影響を受ける `design/api/` 配下の詳細文書
- API 面の責務境界が変わるなら `08_API概念.md`
- 接続や認証の意味が変わるなら `11_接続と認証.md`
- inspection 面の保証が変わるなら `12_デバッグ可能性.md`
- 自発判断の外向き露出が変わるなら `13_自発判断と自発行動.md`
- 現在地や未着手が変わるなら `plan/01_MVP実装計画.md`

<!-- Boundary -->
## 現時点の境界

現時点で正本として定めるのは、`design/api/` 配下にある path、method、認証、request / response 形式である。
内部フローや保存先の exact な shape は、この API 仕様ファミリーの対象に含めない。
