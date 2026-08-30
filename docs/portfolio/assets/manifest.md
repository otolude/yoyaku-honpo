# Portfolio asset manifest

## 1. 監査状態

2026-08-30に、専用開発guildの撮影用private channel `portfolio-demo`と新規合成データだけで4枚を作成した。撮影・匿名化の実施者はOto、repository上のPNG構造・hash・文字列・目視監査者はCodexである。実利用者データ、実運用データ、元画像、編集layerはrepositoryへ含めていない。

公開候補は画素だけを新しいPNGへ再描画し、一覧・詳細の完全な予約UUIDをalphaなしの不透明図形で覆ってflattenしている。ぼかし、mosaic、半透明maskは使用していない。機械監査では全fileのPNG signature、chunk CRC、IEND、trailing dataなしを確認し、chunkは`IHDR`、`sRGB`、`gAMA`、`pHYs`、`IDAT`、`IEND`だけであった。`tEXt`、`zTXt`、`iTXt`、`eXIf`、XMP、EXIFは存在しない。

本manifestは6B-2の組み込み監査を記録する。Discordの最新商標・画面掲載条件、Git履歴、全公開状態の最終判断は6Cに残す。

## 2. Asset records

### `schedule-list.png`

| 項目 | 記録 |
| --- | --- |
| 用途 | 単発・毎日・毎週の予約名、状態、種別、日時、投稿先を示す一覧 |
| 掲載先 | README代表画面、`docs/portfolio/feature-flows.md` |
| Synthetic scenario | 撮影用channelの単発・毎日・毎週予約。表示される予約名とchannel名は新規合成データ |
| Crop | 撮影用予約全5件のうち代表3件を残し、Discord chrome、利用者情報、guild、sidebar、通知と、それより下の非掲載領域を除外 |
| Irreversible redaction | 3件の完全な予約UUIDをalphaなしの不透明図形で覆い、新しいPNGへflatten |
| Metadata removal | 画素を新規PNGへ再描画。privacy関連chunkなしをrepository上で再確認 |
| Dimensions／size | 510 × 425 px／65,173 bytes |
| Identifiers checked | 利用者名・icon、Discord ID、guild／channel ID、UUID、端末情報、秘密・接続情報なし |
| Alt text | 単発・毎日・毎週の合成予約を表示した予約一覧 |
| Capture／review date | 2026-08-30 |
| Creator／reviewer | Oto／Codex |
| SHA-256 | `4ec5b58dc5b29caabd066f958ef433226ae6d63a9b7a0e8c22f2555e5d39d04e` |

### `schedule-detail.png`

| 項目 | 記録 |
| --- | --- |
| 用途 | 毎週予約の状態、日時、本文、詳細操作を示す |
| 掲載先 | README代表画面、`docs/portfolio/feature-flows.md` |
| Synthetic scenario | `週次プロジェクト進捗共有`と撮影用本文・channelだけを使用 |
| Crop | Discord chrome、利用者情報、guild、sidebar、通知、他messageを除外 |
| Irreversible redaction | 完全な予約UUIDをalphaなしの不透明図形で覆い、新しいPNGへflatten |
| Metadata removal | 画素を新規PNGへ再描画。privacy関連chunkなしをrepository上で再確認 |
| Dimensions／size | 510 × 461 px／23,271 bytes |
| Identifiers checked | 利用者名・icon、Discord ID、guild／channel ID、UUID、端末情報、秘密・接続情報なし |
| Alt text | 週次プロジェクト進捗共有の状態・日時・本文・操作を表示した予約詳細 |
| Capture／review date | 2026-08-30 |
| Creator／reviewer | Oto／Codex |
| SHA-256 | `54bc460d0ef0ae3ab74ae54a62f48c98adeb7233b199ad29bb91a8cc09765fdb` |

### `schedule-edit-modal.png`

| 項目 | 記録 |
| --- | --- |
| 用途 | 毎週予約の投稿先、曜日、時刻、終了日、本文を編集するUIを示す |
| 掲載先 | `docs/portfolio/feature-flows.md` |
| Synthetic scenario | `portfolio-demo`と毎週予約の合成値だけを使用 |
| Crop | Modal以外のDiscord chrome、account、guild、sidebar、通知を除外 |
| Irreversible redaction | 実IDを撮影範囲へ含めず、画素だけを新しいPNGへflatten |
| Metadata removal | 画素を新規PNGへ再描画。privacy関連chunkなしをrepository上で再確認 |
| Dimensions／size | 479 × 731 px／22,444 bytes |
| Identifiers checked | 利用者名・icon、Discord ID、guild／channel ID、UUID、端末情報、秘密・接続情報なし |
| Alt text | 合成された毎週予約の投稿先・曜日・時刻・本文を編集するModal |
| Capture／review date | 2026-08-30 |
| Creator／reviewer | Oto／Codex |
| SHA-256 | `cb735c6ae2ef2de032475d518bcd6368788bed7fdd69db5730284f599d22d3a4` |

### `scheduled-post-delivered.png`

| 項目 | 記録 |
| --- | --- |
| 用途 | Botが通常channelへ合成本文を投稿した結果を示す |
| 掲載先 | `docs/portfolio/feature-flows.md` |
| Synthetic scenario | 公開前チェック用の合成本文と公開可能な撮影用Bot名だけを使用 |
| Crop | 投稿message以外のguild、channel chrome、他message、利用者情報を除外 |
| Irreversible redaction | 実IDを撮影範囲へ含めず、画素だけを新しいPNGへflatten |
| Metadata removal | 画素を新規PNGへ再描画。privacy関連chunkなしをrepository上で再確認 |
| Dimensions／size | 472 × 61 px／6,168 bytes |
| Identifiers checked | 利用者名・icon、Discord ID、guild／channel ID、UUID、端末情報、秘密・接続情報なし |
| Alt text | 撮影用channelへBotが投稿した公開前チェックの合成message |
| Capture／review date | 2026-08-30 |
| Creator／reviewer | Oto／Codex |
| SHA-256 | `059a3f948ac124a7e9e32da7aa55e231c687f8ccc55b0d33dd7ac037ab725e81` |

## 3. 非追跡物と後処理

元画像、編集layer、途中画像、backup、local absolute pathは記録・追跡しない。撮影後、毎日・毎週の合成予約はBotの正式操作から論理削除済みで、投稿済み単発予約は既存保持規則に従う。DB直接削除やVolume削除は行っていない。撮影用channelは6C最終監査まで保持する。

## 4. 記録禁止

実guild名、実user／channel／message ID、実UUID、Project／Organization ID、APIキー、接続URL、local absolute path、元画像の所在はmanifestへ記録しない。
