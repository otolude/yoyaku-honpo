# Portfolio asset manifest

## 1. 監査状態

2026-08-31に、専用開発guildの撮影用private channel `portfolio-demo`と新規合成データだけで4枚を再撮影した。撮影・匿名化の実施者は利用者、repository上のPNG構造・hash・文字列・目視監査者はCodexである。実利用者データ、実運用データ、元画像、編集layerはrepositoryへ含めていない。

一覧・詳細の完全な予約UUIDはalphaなしの不透明な黒矩形で覆ってflattenしている。ぼかし、mosaic、半透明maskは使用していない。一覧・詳細はXMPだけを除去し、画像データを保持した。2026-08-31の機械監査では全fileのPNG signature、chunk CRC、IEND、trailing dataなしを確認し、chunkは`IHDR`、`sRGB`、`gAMA`、`pHYs`、`IDAT`、`IEND`だけであった。text、XMP、EXIFを格納するchunkは存在しない。

本manifestは6B-2の組み込み監査と2026-08-31の再撮影監査を記録する。2026-09-02に、Discord UI画像4枚を現在の匿名化・最小crop・非提携表示のままソース閲覧用portfolioへ掲載することを利用者が判断し、dependency license、第三者素材、Discord／OpenAIの商標・画面掲載条件に関する6C項目を完了した。同日に匿名／ログアウト状態でREADME画像とfeature flowsの4画像が正常表示されることを確認し、最終公開受入を完了した。これはDiscordによる無条件な適合保証、公式・公認・提携または非侵害保証を意味しない。

編集Modalと投稿結果には撮影時の開発用Bot表示名 `よやく本舗 Dev` と、利用者が採用したAI生成アイコンが含まれる。アイコンはこの会話の内蔵画像生成機能で新規生成し、第三者画像を参照素材として指定していない。編集時は同機能で生成した直前案を参照した。独立したアイコンfileはrepositoryへ追加しない。AI生成であることは、独占権、非侵害、「よやく本舗」の商標確認完了を保証しない。「よやく本舗」は採用予定名・商標確認未完了として掲載し、公開受入完了後もこの商標上の留保を維持する。差し替え前画像とhashは過去commitの検証証跡として維持する。

## 2. Asset records

### `schedule-list.png`

| 項目 | 記録 |
| --- | --- |
| 用途 | 単発・毎日・毎週の予約名、状態、種別、日時、投稿先を示す一覧 |
| 掲載先 | README代表画面、`docs/portfolio/feature-flows.md` |
| Synthetic scenario | 撮影用channelの単発・毎日・毎週予約。表示される予約名とchannel名は新規合成データ |
| Crop | 撮影用予約全3件と操作欄を残し、Discord chrome、利用者情報、guild、sidebar、通知を除外 |
| Irreversible redaction | 3件の完全な予約UUIDをalphaなしの不透明な黒矩形で覆ってflatten |
| Metadata removal | XMPだけを除去し、画像データは保持。text、XMP、EXIFを格納するchunkなしをrepository上で再確認 |
| Dimensions／size | 512 × 566 px／56,312 bytes |
| Identifiers checked | 利用者名・icon、Discord ID、guild／channel ID、UUID、端末情報、秘密・接続情報なし |
| Alt text | 単発・毎日・毎週の合成予約を表示した予約一覧 |
| Capture／review date | 2026-08-31／2026-08-31 |
| Creator／reviewer | 利用者／Codex |
| SHA-256 | `478c8925e0b5bb051312a7ea7ef7827308dec289fb4366f4d7446d37111f4b62` |

### `schedule-detail.png`

| 項目 | 記録 |
| --- | --- |
| 用途 | 毎週予約の状態、日時、本文、詳細操作を示す |
| 掲載先 | README代表画面、`docs/portfolio/feature-flows.md` |
| Synthetic scenario | `週次プロジェクト進捗共有`と撮影用本文・channelだけを使用 |
| Crop | Discord chrome、利用者情報、guild、sidebar、通知、他messageを除外 |
| Irreversible redaction | 完全な予約UUIDをalphaなしの不透明な黒矩形で覆ってflatten |
| Metadata removal | XMPだけを除去し、画像データは保持。text、XMP、EXIFを格納するchunkなしをrepository上で再確認 |
| Dimensions／size | 512 × 460 px／40,970 bytes |
| Identifiers checked | 利用者名・icon、Discord ID、guild／channel ID、UUID、端末情報、秘密・接続情報なし |
| Alt text | 週次プロジェクト進捗共有の状態・日時・本文・操作を表示した予約詳細 |
| Capture／review date | 2026-08-31／2026-08-31 |
| Creator／reviewer | 利用者／Codex |
| SHA-256 | `e1fdb25700336d4cd6ae58ffb1870f1d18ac0daae4c1fbd438c60e51b3482a91` |

### `schedule-edit-modal.png`

| 項目 | 記録 |
| --- | --- |
| 用途 | 毎週予約の投稿先、曜日、時刻、終了日、本文を編集するUIを示す |
| 掲載先 | `docs/portfolio/feature-flows.md` |
| Synthetic scenario | `portfolio-demo`と毎週予約の合成値、開発用表示名 `よやく本舗 Dev`、AI生成アイコンだけを使用 |
| Crop | Modal以外のDiscord chrome、account、guild、sidebar、通知を除外 |
| Irreversible redaction | 実IDを撮影範囲へ含めておらず、追加redactionなし |
| Metadata removal | text、XMP、EXIFを格納するchunkなしをrepository上で確認 |
| Dimensions／size | 480 × 729 px／36,454 bytes |
| Identifiers checked | 利用者名・icon、Discord ID、guild／channel ID、UUID、端末情報、秘密・接続情報なし |
| Alt text | 合成された毎週予約の投稿先・曜日・時刻・本文を編集するModal |
| Capture／review date | 2026-08-31／2026-08-31 |
| Creator／reviewer | 利用者／Codex |
| SHA-256 | `894eef62dc5a2d8f42455d72e49ae4718cdd301e301f3ee283a37b16bdd1325d` |

### `scheduled-post-delivered.png`

| 項目 | 記録 |
| --- | --- |
| 用途 | Botが通常channelへ合成本文を投稿した結果を示す |
| 掲載先 | `docs/portfolio/feature-flows.md` |
| Synthetic scenario | 公開前チェック用の合成本文、撮影時の開発用Bot表示名 `よやく本舗 Dev`、AI生成アイコンだけを使用 |
| Crop | 投稿message以外のguild、channel chrome、他message、利用者情報を除外 |
| Irreversible redaction | 実IDを撮影範囲へ含めておらず、追加redactionなし |
| Metadata removal | text、XMP、EXIFを格納するchunkなしをrepository上で確認 |
| Dimensions／size | 453 × 54 px／10,601 bytes |
| Identifiers checked | 利用者名・icon、Discord ID、guild／channel ID、UUID、端末情報、秘密・接続情報なし |
| Alt text | 撮影用channelへBotが投稿した公開前チェックの合成message |
| Capture／review date | 2026-08-31／2026-08-31 |
| Creator／reviewer | 利用者／Codex |
| SHA-256 | `f77010f37f9d096c8b4a3a426749a564bd5b02cbfe27fb976a4223635080fb30` |

## 3. 非追跡物と後処理

元画像、編集layer、途中画像、backup、local absolute pathは記録・追跡しない。撮影後、毎日・毎週の合成予約はBotの正式操作から論理削除済みで、投稿済み単発予約は既存保持規則に従う。DB直接削除やVolume削除は行っていない。撮影用channelは6C最終監査まで保持する。

## 4. 記録禁止

実guild名、実user／channel／message ID、実UUID、Project／Organization ID、APIキー、接続URL、local absolute path、元画像の所在はmanifestへ記録しない。
