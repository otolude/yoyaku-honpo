# Portfolio asset manifest

## 現在の状態

6B-1時点でポートフォリオ画像は追加されておらず、実asset recordは0件である。このfileは6B-2で公開用画像を追加する場合の形式だけを定義する。元画像、編集layer、途中画像は記録対象ではなく、Gitへ含めない。

## 記録項目

| Field | Required | Description |
| --- | --- | --- |
| asset file | yes | 公開用assetのrepository相対path。実IDをfile名に含めない |
| status | yes | draft、reviewed、approvedのいずれか |
| synthetic scenario | yes | 架空データだけを使った場面の短い説明 |
| source capture date | yes | 合成環境で撮影した日付 |
| crop | yes | 除外した画面領域 |
| irreversible redaction | yes | 復元不能に焼き込んだ匿名化の有無と対象 |
| metadata removal | yes | metadata除去と再確認の方法 |
| identifiers checked | yes | Discord ID、UUID、server、channel、端末情報等の確認項目 |
| alt text | yes | 公開先で使う説明文 |
| intended placement | yes | READMEまたは詳細文書の掲載位置 |
| reviewer | yes | 公開用assetを確認した担当者名または公開用表記 |
| final review date | yes | 最終確認日 |
| notes | no | 制約。秘密、実ID、local pathを記録しない |

## EXAMPLE ONLY

次は形式例であり、対応する画像fileは存在せず、実asset recordではない。

```yaml
asset_file: EXAMPLE-only-not-a-file.png
status: draft
synthetic_scenario: 架空の毎週予約を表示した詳細画面
source_capture_date: YYYY-MM-DD
crop: サイドバー、通知、他メッセージを除外
irreversible_redaction: 不要。撮影前に合成データ化
metadata_removal: metadata除去後に再読込して確認
identifiers_checked: Discord ID、UUID、server、channel、端末情報
alt_text: 架空の毎週予約の詳細を表示したDiscord画面
intended_placement: READMEの代表画面
reviewer: REVIEWER-TO-BE-DECIDED
final_review_date: YYYY-MM-DD
notes: EXAMPLE ONLY
```

## 記録禁止

実guild名、実user／channel／message ID、実UUID、Project／Organization ID、APIキー、接続URL、local absolute path、元画像の所在はmanifestへ記録しない。
