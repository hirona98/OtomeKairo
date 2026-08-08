# Web UI 通常画面規約

ブラウザ UI（`/ui/`）の通常画面（設定パネル以外）における見た目とマークアップ規約の正本。  
設定パネルは [WebUI設定フォーム規約.md](WebUI設定フォーム規約.md) を正とする。  
API wire、認可、内部状態の表示内容の意味は [../api/README.md](../api/README.md) を正とする。

## 対象

- topbar
- 内部状態パネル（`dashboard-panel`）
- チャット（`chat-panel` / messages / bubble）
- composer（マイク入力・テキスト・画像送信）
- statusbar
- notice（一時通知）

設定パネルのフォーム行・fieldset 規約は対象外（色・フォントなどのトークンは共有する）。

## 領域と密度

| 領域 | 基本フォント | 備考 |
| --- | --- | --- |
| topbar | `--font-title` / body | ブランドと操作 |
| 内部状態 | `--font-secondary` / `--font-micro` | inspection 用の高密度。body に無理に揃えない |
| チャット本文 | `--font-body` | meta は secondary |
| composer | `--font-body` | 操作帯 |
| statusbar | `--font-secondary` | 接続状態 |
| notice | `--font-body` | 一時通知 |

## 展開パネル

通常画面の折りたたみは `details.dashboard-section` を使う。  
設定画面の `advanced-settings` と見た目・密度を共有しない。

## 情報面（info surface）

要約や説明の薄い強調枠は info トークンを使う。

- 内部状態の headline: `.dashboard-headline`
- 設定内の説明枠: `.console-info-box`

色は次のトークンを正とする（`:root`）。

| 用途 | トークン |
| --- | --- |
| 情報枠 | `--info-border` / `--info-bg` / `--info-text` |
| 成功 | `--ok` / `--ok-border` / `--ok-bg` |
| 失敗 | `--danger` / `--error-border` / `--error-bg` |
| 待機・処理中 | `--waiting` / `--waiting-border` / `--waiting-bg` |

chip、badge、`.status.processing` / `.status.error` は上記を共有する。

内部状態パネル背景は `--dashboard-bg`、項目区切りは `--item-line` を使う。

## composer

マイク操作行はツールバーであり、設定の `setting-row` にはしない。

- 入力元 select・Webマイク select・更新は残す。視覚ラベルは付けず、`aria-label` で補う
- 行レイアウトは flex + wrap とし、非表示コントロールが空列を残さない
- 音声の開始/停止は `.mic-toggle`（マイクアイコン、`aria-pressed`）
- 音声状態（停止中・入力中など）は composer 内ではなく statusbar の `#web-microphone-status` に出す
- テキスト送信まわり: 主操作は既定 `button`（送信）、副操作は `.plain-button`、ファイル選択は `.file-button`

## ボタン

| 種別 | 用途 |
| --- | --- |
| 既定 `button` | 主操作（送信、音声開始、適用など） |
| `.plain-button` | 副操作（更新、削除、中止など） |
| `.file-button` | ファイル選択。plain と同系統 |
| `.icon-button` | topbar / 設定ヘッダのアイコン操作 |
| dashboard 内 button | 高密度用に高さと font を micro 寄せてよい |

## チャット吹き出し

- person: 右寄せ・アクセント塗り
- assistant: 左寄せ・assistant 色
- system: 左寄せ・枠付き白

役割差のための配色差は維持する。

## 実装対応

- 静的マークアップ: `src/otomekairo/web/static/index.html`
- スタイル: `src/otomekairo/web/static/styles.css`
- 動的生成: `src/otomekairo/web/static/app.js`（dashboard item、message）

見た目や HTML 構造を固定する自動テストは追加しない。配信可否と HTTP / API 境界のテストのみとする。
