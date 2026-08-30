# スクリーンショット掲載チェックリスト

## 1. 現在の状態

6B-1では画像を作成・編集・追加していない。6B-2の撮影は本チェックリストと[ポートフォリオ掲載計画](../portfolio-plan.md)に従い、専用開発guildの新規合成データだけで行う。既存の実Discord受入画像は使用しない。

## 2. 撮影前

- [ ] Discord画面を掲載するか利用者が決定した。
- [ ] DiscordのUI、名称、ロゴ、商標・ブランド利用条件を確認した。
- [ ] 専用開発guildと、実利用者に紐づかない撮影環境を選んだ。
- [ ] 架空のBot名、利用者名、予約名、本文、channel名を用意した。
- [ ] 個人名、実URL、秘密情報、実Discord ID、実UUIDを合成データに含めていない。
- [ ] 単発、毎日、毎週、一覧、詳細等、必要最小限の撮影一覧を決めた。

## 3. 画面範囲

- [ ] Discordのサイドバー、DM、通知、他メッセージ、avatar、実server名を範囲外にした。
- [ ] user ID、guild ID、channel ID、message ID、public UUIDを表示していない。
- [ ] UUIDが必要な説明は文書用の明示的な架空値を使うか、表示部分を除外した。
- [ ] terminalを含める場合、prompt、PC時刻、host名、user名、パス、履歴を確認した。
- [ ] `.env`、接続URL、token、cookie、Project／Organization ID、APIキーを表示していない。

## 4. 撮影と匿名化

- [ ] 撮影前の合成データ化を、撮影後のマスクより優先した。
- [ ] 追加匿名化は最終画像へ復元不能に焼き込み、半透明塗りや編集layerだけに依存していない。
- [ ] crop後の周辺、thumbnail、clipboard、recent file表示も確認した。
- [ ] EXIF、位置、作成端末等のmetadataを除去した。
- [ ] metadata除去後の公開用fileを再度開いて目視確認した。

## 5. asset仕様

- [ ] GitHub表示に適したPNGまたはWebPを選び、必要以上の解像度・容量にしていない。
- [ ] file名は英小文字、数字、hyphenを使い、実ID、氏名、日時の不要な詳細を含めていない。
- [ ] alt textは画面の目的を説明し、秘密、実ID、未確認の機能主張を含めていない。
- [ ] READMEには理解を助ける最小限の代表画像だけを置いた。
- [ ] 各assetを[manifest](assets/manifest.md)の実recordへ対応付けた。

## 6. Git追加前

- [ ] 元画像、編集layer、途中画像、backup、thumbnailをGit対象外にした。
- [ ] 公開用画像だけを候補にし、追跡前にfile typeとmetadataを再確認した。
- [ ] 実利用者本文、実ID、秘密情報、local pathを文字列検索と目視で確認した。
- [ ] 壊れた画像link、空の枠、未完成asset参照がない。
- [ ] 撮影者とreviewerを分けられない場合、その制約をmanifestへ記録した。

## 7. manifestと最終確認

- [ ] synthetic scenario、source capture date、crop、irreversible redaction、metadata removalを記録した。
- [ ] identifiers checked、alt text、intended placement、reviewer、final review dateを記録した。
- [ ] manifestへ実guild名、実ID、Project ID、秘密、local pathを記録していない。
- [ ] 6Cで全画像の写り込み、metadata、復元可能性、元画像／編集layer非追跡を再監査した。
- [ ] 6Cの商標・第三者素材・公開可否の利用者判断が完了した。

## 8. 限界

焼き込み匿名化とmetadata除去は公開リスクを減らすが、撮影対象の権利や商標条件を自動的に解決しない。最終掲載は6Cの人手監査と利用者判断を必要とする。
