# Web UI 設定フォーム規約

ブラウザ UI（`/ui/`）の設定パネルにおける見た目とマークアップ規約の正本。
API wire、認可、設定の意味境界は [../api/README.md](../api/README.md) と設定系 design 文書を正とする。

## 対象

- 設定パネル（`settings-panel` 配下の各 `tab-page`）
- 設定パネル内で JS が動的生成する行・展開パネル

対象外（トークン共有は可、レイアウト規約は別）:

- チャット本文・composer
- 内部状態ダッシュボード（`dashboard-panel`）

通常画面の見た目規約は [WebUI通常画面規約.md](WebUI通常画面規約.md) を正とする。

## コンポーネント階層

```
.tab-page
  ├── .selector-row          # プリセット選択がある場合のみ（枠外の操作バー）
  └── fieldset.settings-group
        ├── legend
        └── .settings-list
              ├── .setting-row / .setting-row-top
              ├── .checkbox-field
              └── details.advanced-settings
```

- 意味のある節は、1ページ1節でも必ず `fieldset.settings-group` と `legend` で囲む
- プリセット選択行（select + 追加 / 複製 / 削除）は枠外に置く
- プリセット名などの「基本」項目は枠外に出さず、`settings-group` 内に置く

## 項目名と入力の配置

標準は左ラベル・右入力の `.setting-row` とする。

| 用途 | マークアップ |
| --- | --- |
| 通常フィールド | `.setting-row`。左に `label`（for 付き）、右に input / select / 複合コントロール |
| 複数行・ヒント付き | `.setting-row.setting-row-top` + 右列に `.console-control-with-hint` |
| boolean | `.checkbox-field`（`.settings-list` 直下。左ラベル列には載せない） |
| 単位付き数値 | 右列に `.inline-suffix-control` |
| 詳細・一覧の折りたたみ | `details.advanced-settings` |

禁止（設定画面）:

- 上ラベル積み上げの `.form-grid`
- `label` の子に input を入れて縦積みする配置（グローバル `label` スタイルと衝突するため）
- 設定画面用に別見た目の `details` クラスを増やすこと

ラベル文言は「項目名:」を基本とする。

ラベル列幅は `--setting-label-width`（既定 170px）。狭い画面では1列化する。

## 入力幅

入力の横幅は種別クラスで指定する。未指定は右列いっぱい（full）とする。

| クラス | 用途 | 値 |
| --- | --- | --- |
| `.field-width-peek` | APIキーなど、値は保持したまま先頭だけ見せたい入力 | `width: var(--field-width-peek)`（約 4.5em） |
| `.field-width-short` | 整数、秒、ポート、次元、閾値 | `width: var(--field-width-short)`（96px） |
| `.field-width-medium` | プリセット名、表示名、短い ID | `max-width: var(--field-width-medium)`（320px） |
| `.field-width-long` | モデル名、ホスト、コマンド | `max-width: var(--field-width-long)`（480px） |
| `.field-width-full` | URL、パス、textarea、広い select | 右列 100%（既定） |

`.inline-suffix-control` 内の数値入力は short 相当とする。

APIキー入力は `type="password"` で伏せず、通常のテキスト入力 + `.field-width-peek` にする。
値そのものは切り捨てず、横幅だけで先頭付近だけが見えるようにする。


## 展開パネル

設定画面内の折りたたみは `details.advanced-settings` のみを使う。

- TTS 詳細設定
- API 説明の各ドキュメント節

本文がコードや長文の場合は内側に用途別クラス（例: `.api-doc-body`）を置いてよいが、外側の枠・summary 見た目は `advanced-settings` に統一する。

内部状態ダッシュボードの `.dashboard-section` は本規約の対象外。

## フォント

設定画面の文字サイズは次のトークンだけを使う。

| トークン | 値 | 用途 |
| --- | --- | --- |
| `--font-title` | 20px | アプリブランド（topbar） |
| `--font-heading` | 16px | 設定ヘッダ |
| `--font-nav-category` | 14px | 設定ナビのカテゴリ見出し |
| `--font-body` | 12px | 本文、ボタン、legend、summary |
| `--label-size` | body と同じ | 項目ラベル |
| `--field-size` | body と同じ | 入力文字 |
| `--font-secondary` | 11px | 補助が必要な場合のみ |
| `--font-micro` | 10px | ダッシュボード高密度用。設定画面では使わない |

設定画面で 9px や legend 専用の中途半端なサイズをハードコードしない。
差が必要な場合は font-weight でつける。

## 実装対応

- 静的マークアップ: `src/otomekairo/web/static/index.html`
- スタイル: `src/otomekairo/web/static/styles.css`
- 動的生成: `src/otomekairo/web/static/app.js`

見た目や HTML 構造を固定する自動テストは追加しない。配信可否と HTTP / API 境界のテストのみとする。
