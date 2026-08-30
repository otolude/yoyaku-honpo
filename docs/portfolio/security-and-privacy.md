# 安全性とプライバシー

## 1. 表現上の前提

本書は実装した境界と確認範囲を記録するもので、絶対的な安全性、SLA、外部認証、第三者監査済みを主張しない。現在は単一設定guildのローカル開発・ポートフォリオ段階である。

## 2. 実装済みのInteraction境界

- commandとModal submitの現在のInteractionからguild、owner／administrator、許可ロール、DM境界を再検証する。
- 詳細表示後に権限を失った場合は古いEmbedとViewを解除し、固定案内だけを`AllowedMentions.none()`付きで返す。
- 予約内容を扱う応答はephemeralを基本とし、mentionの意図しない展開を抑える。
- View、Button、SelectとModal内入力のcustom IDは固定用途値とし、業務IDや本文を含めない。
- 外側Modalだけは固定prefixと非識別nonceを使い、別利用者・別予約・別端末のdispatchを分離する。
- channelの存在と権限はcache-onlyで再検証し、操作中の暗黙REST取得に依存しない境界を持つ。

## 3. transactionと競合

- Application Serviceがtransaction境界を持ち、RepositoryのORMをBotへ返さない。
- 更新対象をrow lockし、expected version、状態、内容、認可をtransaction内で再検証する。
- staleな操作は固定競合案内で拒否し、別予約、version、OperationLogを変更しない。
- AI結果はSchedule version、source、本文、状態をCAS条件にし、manual名を上書きしない。
- Generator実行中はDB Session、transaction、row lock、ORMを保持しない。

## 4. ログと保存の情報境界

- APIキー、DB資格情報、接続URL、予約本文、生成名、Provider応答全文、Provider例外全文を通常ログへ出さない。
- OperationLogは固定actionと必要最小限の変更種別を記録し、手動名・AI生成名の全文を保存しない。
- 予約名生成Job／Budgetには本文、prompt、応答、生成名、UUID、Discord ID、guild ID、契約・プラン情報を保存しない。
- Provider request IDとraw usageをDBへ保存しない。
- AI入力・応答履歴、文体学習、過去投稿学習、Embedding、利用者プロフィールを作らない。

これはBot自身のデータ最小化方針であり、外部Provider側の学習不使用・保持条件とは別の境界である。実Providerの保持、請求、dashboard設定は未確認で、公開前受入へ延期している。

## 5. AI Provider境界

- AIとProviderは初期disabledで、APIキー未設定でも基本予約機能を起動できる。
- Provider、許可model、単価、為替、token上限、最大費用のいずれかが不明ならAdapterを利用可能にしない。
- Provider呼出前に運営Budgetを悲観予約し、timeout、cancel、結果不明でも返却しない。
- SDKの自動retryを0とし、同じJobからProviderを再呼出ししない。
- 出力を構造、件数、単一行、32文字、Unicode禁止categoryで再検証し、Domain validatorを最終防御とする。
- Providerへ現在本文と固定生成条件だけを送り、Discord ID、UUID、時刻、投稿先、契約情報を送らない。
- AI無効、Budget上限、Provider障害でもJSTフォールバック名、手動名編集、予約作成・投稿を継続する。

OpenAI Adapterと手動受入CLIは無通信のMock transportで確認済みだが、実API品質や本番運用を確認済みとはしない。

## 6. Migrationと環境分離

- 正式なMigration経路はPythonラッパーに限定する。
- ラッパーと`alembic/env.py`がtarget、期待DB名、operation、confirmationを独立して検証する。
- URL上のDB名に加え、接続後の`current_database()`を完全一致照合してからMigration contextを開始する。
- testは専用test URLだけを使い、development URLや`.env`へfallbackしない。
- productionは期待DB名とprocessのURLを明示できる環境が確定するまで拒否する。
- URL、user、host、port、passwordを通常出力や例外へ含めない。

## 7. 実装済みと将来方針の区別

| 項目 | 現在の状態 |
| --- | --- |
| guild／owner／administrator認可、再認可、CAS | 実装済み・自動隔離テスト済み。一部は実Discord確認済み |
| Migration二層ガードとtest DB fail-closed | 実装済み・専用PostgreSQLで隔離確認済み |
| AI Job／Budget／Adapter | 基盤実装済み・無通信テスト済み・初期disabled |
| 実Provider保持・請求・品質 | 未確認。公開前受入へ延期中 |
| ARM64 Linux | 未確認。公開前配置環境で確認予定 |
| 常時稼働、監視、バックアップ自動化 | 未実装または配置環境未確定 |
| Plan、Entitlement、顧客Quota、決済 | 未実装 |
| カード情報をBot DBへ保存しない | 将来の決済設計方針。決済自体は未実装 |
| SECURITY報告窓口、外部監査・認証 | 未決定・未実施 |

## 8. 公開前に残る確認

- Git履歴、tracked files、画像metadata、復元可能性を含む秘密情報監査
- Discord／OpenAIの名称、UI、商標・画面掲載条件
- dependency license、LICENSE、著作権表記、SECURITY連絡先
- 隔離環境でのREADME再現と、配置環境の監視・バックアップ・ARM64確認
- 実Providerを明示許可後に限定実行する品質・保持・請求受入

公開前の具体的な判定は[ポートフォリオ掲載計画](../portfolio-plan.md)の6Cへ残す。
