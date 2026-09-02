# Security Policy

## Supported version

公開中の開発段階では、`develop`ブランチの最新状態だけを調査・修正対象とします。過去commit、fork、改変版への対応は保証しません。正式リリース前であり、返信時間や修正期限も保証しません。

## Reporting a vulnerability

セキュリティ上の問題を公開Issueへ投稿しないでください。Private vulnerability reportingは有効です。repositoryのSecurity画面またはIssue作成画面にあるGitHub標準の`Report a vulnerability`導線から非公開で報告してください。個人メール、Discord DM、外部連絡先は現段階では設けていません。

報告には、秘密を含まない概要、影響範囲、最小限の再現条件を記載してください。次の情報は添付しないでください。

- Bot token、API key、Authorization header、cookie
- Database URL、password、Webhook本文
- 実利用者の投稿本文、予約名、個人情報
- Discord user／guild／channel／message ID、予約UUID
- `.env`、database dump、未加工log、screenshot

再現に実tokenや実利用者データを必要とする手順は避け、匿名の合成値へ置き換えてください。受領後は影響と再現性を確認しますが、採用、返信、修正、公開時期を約束するものではありません。
