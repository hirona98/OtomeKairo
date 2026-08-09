# Web UI 通常画面規約

ブラウザ UI（`/ui/`）の通常画面（設定パネル以外）における見た目とマークアップ規約の正本。  
設定パネルは [WebUI設定フォーム規約.md](WebUI設定フォーム規約.md) を正とする。  
API wire、認可、内部状態の表示内容の意味は [../api/README.md](../api/README.md) を正とする。

## 対象

- topbar（状態メニューを含む）
- いまパネル（`dashboard-panel`。旧称・内部状態）
- チャット（`chat-panel` / messages / bubble）
- composer（マイク入力・テキスト・画像送信）
- statusbar
- notice（一時通知）

設定パネルのフォーム行・fieldset 規約は対象外（色・フォントなどのトークンは共有する）。
ログ専用画面（`/ui/logs`）と判断専用画面（`/ui/cycles`）は対象外とする。色トークンは共有してよいが、専用レイアウトを持つ。
確認面の役割分担（いま / 判断 / ログ）は [../runtime/デバッグ可能性.md](../runtime/デバッグ可能性.md) を正とする。


## 領域と密度

| 領域 | 基本フォント | 備考 |
| --- | --- | --- |
| topbar | `--font-title` / body | ブランドと操作 |
| いま | `--font-secondary` / `--font-micro` | 現在の個の高密度要約。body に無理に揃えない |
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

- 保存済み入力元を表示し、ブラウザマイク選択時だけWebマイク select を表示する
- 行レイアウトは flex + wrap とし、非表示コントロールが空列を残さない
- マイク ON/OFF は `.mic-toggle`（マイクアイコン、`aria-pressed`）。正本は選択中アバターの `stt.enabled` であり、`audio_runtime_state.stt_enabled` を表示する
- その隣に音声合成 ON/OFF の `.mic-toggle`（スピーカーアイコン、`#toggle-web-tts`）。正本は `tts.enabled` であり、`audio_runtime_state.tts_enabled` を表示する
- `web_microphone` のときだけ STT ON に合わせてブラウザ capture と Web 入力 session を開始する
- 音声状態（停止中・入力中・STT 無効など）は composer 内ではなく statusbar の `#web-microphone-status` に出す
- マイク操作行の直下に音声観測行（`.audio-meters`）を置く
  - 入力レベル（`vad.dbfs`）
  - VAD しきい値超過（`vad.probability` と threshold の比較。`vad.speaking` は区間中バッジ）
  - 識別しきい値超過（直近発話の `top1_similarity` / `threshold_met`。連続更新ではない）
  - データは `audio_runtime_state` を使い、ブラウザ側で VAD / 話者識別を再計算しない
  - バー表示スケールは直近ピークやしきい値に依存させず固定する
    - 入力レベル: -60 dBFS を 0%、**-12 dBFS を 100%**（超過は clip。数値テキストは実 dBFS）
    - VAD probability / 識別 similarity: **0.0 を 0%、1.0 を 100%**（しきい値はマーカーのみ）
  - 数値列・バッジ列は固定幅とし、表示内容の有無や桁変化でバー（track）幅を変えない
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
