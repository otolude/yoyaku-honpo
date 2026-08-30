# Security Policy

## Supported version

公開前の開発段階では、`develop`ブランチの最新状態だけを調査・修正対象とします。過去commit、fork、改変版への対応は保証しません。一般公開・正式リリース前であり、返信時間や修正期限も保証しません。

## Reporting a vulnerability

セキュリティ上の問題を公開Issueへ投稿しないでください。public化直前にGitHub Private vulnerability reportingを有効化し、以後はrepositoryのSecurity画面にあるprivate reporting経路を使用する方針です。まだその機能が利用できない場合は、秘密情報を公開せず、機能の有効化後に報告してください。個人メール、Discord DM、外部連絡先は現段階では設けていません。

報告には、秘密を含まない概要、影響範囲、最小限の再現条件を記載してください。次の情報は添付しないでください。

- Bot token、API key、Authorization header、cookie
- Database URL、password、Webhook本文
- 実利用者の投稿本文、予約名、個人情報
- Discord user／guild／channel／message ID、予約UUID
- `.env`、database dump、未加工log、screenshot

再現に実tokenや実利用者データを必要とする手順は避け、匿名の合成値へ置き換えてください。受領後は影響と再現性を確認しますが、採用、返信、修正、公開時期を約束するものではありません。
