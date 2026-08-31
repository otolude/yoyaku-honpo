# スクリーンショット掲載チェックリスト

## 1. 現在の状態

6B-2では本チェックリストと[ポートフォリオ掲載計画](../portfolio-plan.md)に従い、専用開発guildの新規合成データだけで4画像を作成・監査した。既存の実Discord受入画像は使用していない。実施結果は本書7節の後に記録し、Discord商標条件と公開可否を含む6C条件は未確認のまま分離する。

## 2. 撮影前

- [x] Discord画面を掲載するか利用者が決定した。
- [ ] DiscordのUI、名称、ロゴ、商標・ブランド利用条件を確認した。
- [x] 専用開発guildと、実利用者に紐づかない撮影環境を選んだ。
- [x] 架空のBot名、利用者名、予約名、本文、channel名を用意した。
- [x] 個人名、実URL、秘密情報、実Discord ID、実UUIDを合成データに含めていない。
- [x] 単発、毎日、毎週、一覧、詳細等、必要最小限の撮影一覧を決めた。

## 3. 画面範囲

- [x] Discordのサイドバー、DM、通知、他メッセージ、avatar、実server名を範囲外にした。
- [x] user ID、guild ID、channel ID、message ID、public UUIDを表示していない。
- [x] UUIDが必要な説明は文書用の明示的な架空値を使うか、表示部分を除外した。
- [x] terminalを含める場合、prompt、PC時刻、host名、user名、パス、履歴を確認した。
- [x] `.env`、接続URL、token、cookie、Project／Organization ID、APIキーを表示していない。

## 4. 撮影と匿名化

- [x] 撮影前の合成データ化を、撮影後のマスクより優先した。
- [x] 追加匿名化は最終画像へ復元不能に焼き込み、半透明塗りや編集layerだけに依存していない。
- [x] crop後の周辺、thumbnail、clipboard、recent file表示も確認した。
- [x] EXIF、位置、作成端末等のmetadataを除去した。
- [x] metadata除去後の公開用fileを再度開いて目視確認した。

## 5. asset仕様

- [x] GitHub表示に適したPNGまたはWebPを選び、必要以上の解像度・容量にしていない。
- [x] file名は英小文字、数字、hyphenを使い、実ID、氏名、日時の不要な詳細を含めていない。
- [x] alt textは画面の目的を説明し、秘密、実ID、未確認の機能主張を含めていない。
- [x] READMEには理解を助ける最小限の代表画像だけを置いた。
- [x] 各assetを[manifest](assets/manifest.md)の実recordへ対応付けた。

## 6. Git追加前

- [x] 元画像、編集layer、途中画像、backup、thumbnailをGit対象外にした。
- [x] 公開用画像だけを候補にし、追跡前にfile typeとmetadataを再確認した。
- [x] 実利用者本文、実ID、秘密情報、local pathを文字列検索と目視で確認した。
- [x] 壊れた画像link、空の枠、未完成asset参照がない。
- [x] 撮影者とreviewerを分けられない場合、その制約をmanifestへ記録した。

## 7. manifestと最終確認

- [x] synthetic scenario、source capture date、crop、irreversible redaction、metadata removalを記録した。
- [x] identifiers checked、alt text、intended placement、reviewer、final review dateを記録した。
- [x] manifestへ実guild名、実ID、Project ID、秘密、local pathを記録していない。
- [ ] 6Cで全画像の写り込み、metadata、復元可能性、元画像／編集layer非追跡を再監査した。
- [ ] 6Cの商標・第三者素材・公開可否の利用者判断が完了した。
- [x] 採用予定名「よやく本舗」で必要な画面を再撮影し、既存画像を差し替えて新しいSHA-256を監査した。

## 8. 実施結果と限界

### 8.1 2026-08-30の6B-2実施結果

- Discord画面掲載と、撮影用private channel `portfolio-demo`の使用は利用者承認済みである。
- Otoが新規合成データだけで一覧、詳細、編集Modal、投稿結果を撮影・匿名化した。
- 利用者名・icon、guild、sidebar、DM、通知、端末情報、Discord ID、guild／channel IDを掲載していない。
- 一覧・詳細の完全な予約UUIDは、alphaなしの不透明図形で覆って新規PNGへflattenした。ぼかし、mosaic、半透明maskは使用していない。
- 元画像、編集layer、途中画像はrepositoryへ置かず、公開候補4枚だけを`assets/`へ配置した。
- repository上でPNG signature、寸法、chunk CRC、IEND、trailing dataなしを確認した。privacy関連のtext／EXIF／XMP chunkは存在しない。
- 4枚を独立して目視し、写り込み、復元可能なmask、不自然な欠け、判読不能な主要表示がないことを確認した。一覧は代表3件より下を意図的にcropした旨をcaptionとmanifestへ記録した。
- file名、alt text、用途、掲載先、匿名化、metadata、確認日、確認者、SHA-256を[manifest](assets/manifest.md)へ対応付けた。
- 投稿結果画像の `D AI Reminder Bot Dev` は撮影時の開発用表示名であり、現在の採用予定名ではない。既存画像とSHA-256は過去証跡として維持し、再撮影は公開前の後続事項とした。
- 毎日・毎週の合成予約はBotの正式操作で論理削除し、投稿済み単発予約は既存保持規則に従う。撮影用channelは6Cまで保持する。

6Cで行う全画像・Git履歴・商標・公開状態の最終監査は、この6B-2記録によって完了扱いにしない。

### 8.2 2026-08-31の再撮影・差し替え監査

- 利用者が新規合成データだけで4枚を再撮影し、開発用表示名 `よやく本舗 Dev` を反映した。
- 一覧は撮影用予約全3件と操作欄を表示しており、旧画像の「全5件中の代表3件をcrop」は適用しない。
- 一覧の予約UUID 3か所と詳細の予約UUID 1か所は、alphaなしの不透明な黒矩形で覆ってflattenした。
- 一覧・詳細はXMPだけを除去して画像データを保持した。4枚ともchunkは`IHDR`、`sRGB`、`gAMA`、`pHYs`、`IDAT`、`IEND`だけで、text、XMP、EXIFを格納するchunkは存在しない。
- 編集Modalと投稿結果には `よやく本舗 Dev` とAI生成アイコンが写っている。アイコンはこの会話の内蔵画像生成機能で第三者参照素材を指定せず新規生成し、編集時は同機能の直前案を参照した。独占権・非侵害・商標確認完了は保証しない。
- 元画像、編集layer、途中画像、独立したアイコンfileはrepositoryへ追加せず、公開用4枚だけを差し替えた。
- 撮影日と監査日は2026-08-31で、寸法、size、SHA-256、加工方法は[manifest](assets/manifest.md)へ記録した。
- 「よやく本舗」は採用予定名であり、商標確認、6Cの法的条件、最終公開判断は未完了のまま維持する。

### 8.3 限界

焼き込み匿名化とmetadata除去は公開リスクを減らすが、撮影対象の権利や商標条件を自動的に解決しない。最終掲載は6Cの人手監査と利用者判断を必要とする。
