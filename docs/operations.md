# Phase 1 運用Runbook

## 1. 文書の目的と対象環境

本書は、Phase 1・Phase 2完了時点のDiscord予約投稿Botをローカル環境で開発・運用し、障害時に安全に判断するためのRunbookである。対象は環境変数で指定した1つのDiscord guild、WSL2またはLinux上の通常版CPython 3.14、Docker Compose上のPostgreSQL 18.4である。一般公開用の常時稼働環境は未構築であり、公開準備まで構築を保留する。

コマンドは特記がない限りリポジトリルートで実行する。開発DB、テストDB、本番DBを取り違えないことを最優先とし、実行前に接続先を確認する。予約の運用識別子には内部DB IDではなく完全なpublic UUIDv7を使う。

基本セットアップは[README](../README.md)、仕様は[要件書](requirements-beta.md)と[技術設計書](technical-design-beta.md)、今後の順序は[開発・公開ロードマップ](development-roadmap.md)、実Discord確認は[Phase 1手動受入](manual-acceptance-phase1.md)と[Phase 2手動受入](manual-acceptance-phase2.md)を参照する。

`/post list` は全件数・総ページ数を表示し、前後ボタン、予約種類（すべて・単発・毎日・毎週）の絞り込み、現在ページの予約選択で詳細を確認できる。種類を変えると状態条件を保ったまま1ページへ戻り、0件でも別種類へ切り替えられる。一覧・詳細ViewはBotが継続稼働している間は15分で無効化せず操作できる。Bot再起動後の古い画面は復元しないため、`/post list` または `/post show` を再実行する。操作のたびに最新状態と権限を確認するため、操作間の作成・変更・削除により表示位置が変わることは正常である。終了日は `明日`、`8/30`、`2026-08-30` などを指定でき、成功表示では常に完全な `YYYY-MM-DD` となる。

## 2. コンポーネント構成

- Discord Bot runtime: guild限定スラッシュコマンド、起動停止、3つのloopを管理する。
- PollingWorker: due runをclaimし、投稿・結果保存・次回run確定を行う。
- NotificationWorker: NotificationLog outboxを処理し、通知とfallbackを行う。
- Startup Recovery: processing、pending、notification lease、draft通知計画を復旧する。
- CleanupService: 終端予約とglobal通知を保持期限後に物理削除する。
- PostgreSQL: Schedule、Run、DeliveryAttempt、OperationLog、NotificationLog、NotificationAttemptを永続化する唯一の正本である。
- Discord Gateway: 投稿と通知のDiscord境界であり、通信中にDB transactionや行ロックを保持しない。

Sessionとtransactionはorchestration boundaryが所有する。Botコマンドやruntimeに加え、PollingWorker、NotificationWorker、CleanupServiceも短いtransactionを構成できる。Repositoryと業務Domain/Application Serviceはcommit・rollbackしない。

## 3. 環境変数

設定名と安全な見本は[`.env.example`](../.env.example)を参照する。`.env`の内容を表示したり、チャットやIssueへ貼ったりしない。

必須:

- `APP_ENV`
- `TIMEZONE=Asia/Tokyo`
- `DISCORD_BOT_TOKEN`
- `DISCORD_GUILD_ID`
- `DISCORD_ALLOWED_ROLE_IDS`
- `DISCORD_OPERATOR_USER_ID`
- `DISCORD_OPERATOR_CHANNEL_ID`
- `DATABASE_URL`

任意または既定値あり:

- `LOG_LEVEL`
- `SCHEDULER_POLL_INTERVAL_SECONDS`
- `SCHEDULER_BATCH_SIZE`
- `SCHEDULER_MAX_CONCURRENCY`
- `SCHEDULER_PROCESSING_TIMEOUT_SECONDS`
- `NOTIFICATION_POLL_INTERVAL_SECONDS`
- `NOTIFICATION_BATCH_SIZE`
- `NOTIFICATION_MAX_CONCURRENCY`
- `NOTIFICATION_PROCESSING_TIMEOUT_SECONDS`

起動時検証に失敗した場合はDiscordへ接続しない。tokenとDB URLはSecretStrで保持し、通常ログへ出さない。

## 4. Discord設定

Discord Developer PortalでapplicationとBotを作成し、開発用guildへ次を付けて導入する。

- OAuth2 scopes: `bot`、`applications.commands`
- Bot権限: View Channels、Send Messages、Embed Links
- Privileged Gateway Intents: すべて不要
- Message Content Intent: 不要
- Server Members Intent: 不要
- Presence Intent: 不要
- Administrator: 不要

Botが投稿する各チャンネルとoperator channelで、閲覧・送信・Embed表示が許可されていることを確認する。許可ロールIDはコマンド作成権限に使用するが、非管理者が他人の予約を操作する権限にはならない。

## 5. 初回起動

詳細は[READMEの初回セットアップ](../README.md#初回セットアップ)に従う。要点は次の順序である。

1. `.venv`を作成して依存関係を導入する。
2. `.env.example`からGit管理対象外の`.env`を作る。
3. `docker compose config`で定義を確認する。
4. 開発用`postgres`だけを起動する。
5. healthコマンドで`SELECT 1`を確認する。
6. 管理されたAlembic手順で`upgrade head`を行う。
7. `current`、`heads`、`check`を確認する。
8. Botを起動する。

```bash
source .venv/bin/activate
docker compose config
docker compose up -d postgres
python -m discord_ai_reminder_bot.infrastructure.database.health
python -m discord_ai_reminder_bot.infrastructure.database.migrate --target development --expected-database discord_bot_dev --confirm development:discord_bot_dev:upgrade upgrade head
python -m discord_ai_reminder_bot.infrastructure.database.migrate --target development --expected-database discord_bot_dev current
python -m discord_ai_reminder_bot.infrastructure.database.migrate heads
python -m discord_ai_reminder_bot.infrastructure.database.migrate --target development --expected-database discord_bot_dev check
python -m discord_ai_reminder_bot
```

実装上の起動順は次のとおりである。

1. 設定読込と検証
2. Engine／Session factoryとBotオブジェクト作成
3. Discord Client起動過程の`setup_hook`
4. DBのAlembic revisionと単一headを読み取り専用確認
5. configured guildへのコマンド同期
6. Discord ready
7. Clockから固定UTC `recovery_cutoff`を1回取得
8. Processing Recovery
9. Pending Startup Recovery
10. Notification Recovery
11. Draft Notification Bootstrap
12. 全完了後にRecovery Eventをset
13. 投稿loop、通知loop、maintenance loopを開始

Schema確認はDiscord readyより前の`setup_hook`で行われる。Recoveryはready後である。

## 6. 通常起動

[READMEの日常の作業開始](../README.md#日常の作業開始)に従う。

```bash
git status --short --branch
source .venv/bin/activate
docker compose up -d postgres
docker compose ps
python -m discord_ai_reminder_bot.infrastructure.database.health
python -m discord_ai_reminder_bot.infrastructure.database.migrate --target development --expected-database discord_bot_dev current
python -m discord_ai_reminder_bot.infrastructure.database.migrate heads
python -m discord_ai_reminder_bot
```

`database_schema_verified`、`application_commands_synced`、各Recovery完了、`startup_recovery_complete`を確認する。Recoveryが未完了なら3 loopは開始されない。

## 7. 正常停止

Botのターミナルで`Ctrl+C`を1回押し、プロセス終了まで待つ。実装上の停止順は次のとおりである。

1. closing状態にして新規処理開始を抑止
2. 投稿、通知、maintenanceの3 loopをstop、cancel、Task回収
3. startup Recovery Taskをcancel・回収
4. 作成・削除の確認Viewを停止・回収
5. Discord Clientをclose
6. DB Engineをdispose

終了後、開発用PostgreSQLだけを停止する。

```bash
docker compose stop postgres
docker compose ps
git status --short --branch
```

名前付きVolume `postgres_data`は維持する。プロジェクト全体やVolumeをまとめて削除するCompose操作を通常停止に使わない。

## 8. Schema revision不一致

BotはMigrationを自動適用しない。DBのrevisionが単一のscript headと一致しない、revisionが複数ある、未知のrevisionである場合は起動を中止する。

Migrationの正式経路はPythonラッパーだけである。ラッパーと`alembic/env.py`は、target、期待DB名、操作確認を独立検証し、接続後の`SELECT current_database()`が期待DB名と完全一致するまでMigration contextを開始しない。testは`discord_bot_test`、developmentは`discord_bot_dev`だけを許可する。productionは実DB名の明示が必須であり、未確定の名前を推測しない。

`MIGRATION_TARGET_ENV`、`MIGRATION_EXPECTED_DATABASE`、`MIGRATION_APPLY_CONFIRMATION`は直接CLIに対する最終ガード用で、`.env`から暗黙に読み込ませない。正式ラッパーでは同じ値を`--target`、`--expected-database`、`--confirm`として実行ごとに明示する。test URLは実行プロセスの`TEST_DATABASE_URL`だけ、development URLは既存の`DATABASE_URL`／`.env`、production URLは実行プロセスの`DATABASE_URL`だけから選ぶ。URL、user、host、port、passwordを通常ログやコマンド引数へ出さない。

1. Botを停止したままにする。
2. `.env`を表示せず、対象環境と接続先が正しいか別の安全な管理手段で確認する。
3. 本番相当環境なら先に[バックアップ](#16-バックアップ)を取得する。
4. 状態を読み取る。

```bash
python -m discord_ai_reminder_bot.infrastructure.database.migrate --target development --expected-database discord_bot_dev current
python -m discord_ai_reminder_bot.infrastructure.database.migrate history
python -m discord_ai_reminder_bot.infrastructure.database.migrate heads
python -m discord_ai_reminder_bot.infrastructure.database.migrate --target development --expected-database discord_bot_dev check
```

5. Revision鎖と変更手順をレビューする。
6. 承認された接続先にだけ適用する。

```bash
python -m discord_ai_reminder_bot.infrastructure.database.migrate --target development --expected-database discord_bot_dev --confirm development:discord_bot_dev:upgrade upgrade head
python -m discord_ai_reminder_bot.infrastructure.database.migrate --target development --expected-database discord_bot_dev current
python -m discord_ai_reminder_bot.infrastructure.database.migrate --target development --expected-database discord_bot_dev check
```

7. Botを再起動する。

Alembic CLIを直接実行しない。offline modeは禁止する。`downgrade`と`stamp`は通常運用で使用せず、既存Revision固有guardを含む個別手順の承認と、操作に束縛された確認値がある場合だけラッパー内部で許可する。過去Revisionの書換え、手動DDL、`alembic_version`の直接更新も行わない。

## 9. PostgreSQL障害

- Botは安全な固定ERRORイベントを記録し、資格情報やdriver例外全文を出さない。
- DB障害中はBotを多重起動せず、接続先、PostgreSQLコンテナ状態、容量、ホスト側障害を確認する。
- 開発環境では次の読み取り中心の確認を行う。

```bash
docker compose ps
python -m discord_ai_reminder_bot.infrastructure.database.health
```

- 復旧後はSchema revisionを確認し、Botを1プロセスだけ起動する。
- Recovery完了ログを確認する。未完了なら通常loopは始まらない。
- DB障害を直す目的でVolume削除、手動状態更新、テーブル再作成を行わない。

## 10. Discord接続障害

- Botは`reconnect=True`で起動する。
- 同一プロセス内の再接続ではguild同期とStartup Recoveryを重複実行しない。
- Discord status、network、Bot tokenの有効性、guild所属、チャンネル権限を確認する。
- tokenをログ、コマンド引数、画面共有へ出さない。
- 再接続を促すために複数Botプロセスを同時起動しない。

## 11. 投稿失敗・unknown

- transient: 一時障害。1分後、5分後、15分後の順に再試行する。
- permanent: 権限不足、チャンネル不存在など。再試行せず最終failedとする。
- Rate Limit: Discordが示す未来のRetry-Afterを通常間隔より優先する。
- unknown: `sending`確定後に結果を保存できないなど、投稿成否を断定できない状態。二重投稿防止のため自動再送しない。
- 最大試行数: 初回を含む4回。

運営者は通知Embedにある完全な予約UUIDv7、JST予定日時、投稿先、状態、対応案内を確認する。対象チャンネルの履歴とDiscord側の監査可能情報を確認し、投稿済みか断定できないunknownを手動で再送しない。必要な対応後、権限規則に従って予約を管理する。

投稿本文、token、DATABASE_URL、内部例外全文をログや障害票へ載せない。

## 12. 通知失敗とfallback

論理通知の経路は次の順である。

1. creator DM
2. operator channel
3. operator DM
4. log

通知は初回を含め最大3回で、transientは1分後と5分後に再試行する。Rate Limitは未来のRetry-Afterを優先する。permanentまたは3回上限時だけ次経路を別のNotificationLogとして冪等生成する。unknownは再送もfallbackもしない。送信前再検証で不要になったstale通知はcancelledとし、Discordへ送らない。

Discord経路は固定タイトル・説明、日本語状態、投稿先チャンネルメンション、JST日時、完全なUUIDv7、対応案内を持つEmbedを使う。投稿本文・プレビューを含めず、`AllowedMentions.none()`相当ですべての通知を無効にする。`log`経路はDiscord Embedを送らず、`notification_log_route_terminal`という固定ERRORイベントを記録する。

## 13. 起動時Recovery

全Recoveryは1回の固定UTC `recovery_cutoff`を共有し、それぞれ最大25バッチで処理する。

- Processing Recovery: processing lease期限切れを取得する。送信前のclaimedは試行回数に応じてpendingへ戻せる。sending、unknown、不整合は自動再送せずfailedへ確定する。
- Pending Startup Recovery: 期限超過pendingを整理する。単発activeの初回runは15分以内（ちょうど15分を含む）なら遅延投稿対象、15分超過なら投稿せずfailedにする。pausedの健全な通常初回runは再開選択用に保持し、paused retryまたは不整合pendingは安全側でskipする。
- retry pending: 15分ルールの対象外で、保存済み`next_attempt_at`に従って維持する。
- PollingWorkerのrun claimは親Scheduleがactiveまたはdraftの場合だけ行う。paused保持runはdueになってもclaimせず、送信前のpaused再検証も防御層として残す。
- 定期欠落回: 過去回をskippedで記録し、cutoffより厳密に未来の未使用runを1件生成する。1 Schedule・1起動単位で集約通知する。
- Notification Recovery: expired claimedは通知の再試行規則へ戻し、sendingはunknownとして再送しない。
- Draft Notification Bootstrap: 過ぎた24時間前・1時間前通知を後追いせずcancelし、未来draft runに必要な計画だけを冪等作成する。残り1時間未満ならimmediateを計画する。

25バッチ目が満杯、定期欠落回が安全上限を超過、またはどこかで失敗した場合、Recovery Eventをsetせず、投稿・通知・maintenance loopを開始しない。先に成功したバッチはcommit済みであり、次回起動で残りを処理する。同一プロセスのDiscord再接続ではRecoveryを重複実行しない。

## 14. cleanup

- 実行時刻: 毎日JST 04:00
- 起動直後には実行せず、全Startup Recovery完了後にloopを開始する。
- 対象: `completed`、`ended`、`deleted`
- 基準: `terminal_at`から30日。30日ちょうどを含む。
- 対象外: `failed`のままの予約、その他の非終端状態。
- pending/processing Run、claimed/sending DeliveryAttempt、pending/processing NotificationLog、claimed/sending NotificationAttemptがあれば削除しない。
- ScheduleとRunの両方に関連しない終端global通知も`finished_at`から30日後に削除する。
- 1対象1 Session・1 transaction、`SET LOCAL lock_timeout = '1s'`、`FOR UPDATE SKIP LOCKED`を使用する。
- 1サイクル最大Schedule 100件、global通知100件。101件目以降や失敗対象があれば`incomplete`となり、次回へ残す。
- FKのRESTRICT順にNotificationAttempt、NotificationLog、DeliveryAttempt、OperationLog、ScheduleRun、Scheduleを削除する。
- cleanupの失敗・未完了は投稿loopと通知loopを停止しない。

開発DBでcleanupを手動実行するCLIは実装されていない。存在しない管理コマンドを作らず、手動受入は専用テスト環境と既存テスト境界で行う。

## 15. ログと監視

固定イベント名を監視条件に使用し、例外全文や可変の秘密値を検索キーにしない。

主なINFO:

- `database_schema_verified`
- `application_commands_synced`
- `startup_pending_recovery_complete`
- `startup_notification_recovery_complete`
- `startup_draft_notification_bootstrap_complete`
- `startup_recovery_complete`
- `poll_cycle_complete`
- `notification_poll_cycle_complete`
- `maintenance_cleanup_cycle_complete`

主なERROR:

- 起動: `bot_run_failed`、`startup_recovery_failed`、`startup_recovery_incomplete`
- Recovery上限: `startup_pending_recovery_incomplete`、`startup_notification_recovery_incomplete`、`startup_draft_notification_bootstrap_incomplete`
- 投稿poll: `poll_cycle_failed`、`poll_claim_failed`、`poll_task_unexpected_failure`、`poll_item_internal_error`、`delivery_success_persist_failed`
- 通知: `notification_poll_cycle_failed`、`notification_claim_failed`、`notification_task_unexpected_failure`、`notification_item_internal_error`、`notification_success_persist_failed`、`notification_log_route_terminal`
- maintenance: `maintenance_cleanup_cycle_failed`
- shutdown: `database_engine_dispose_failed`
- コマンド境界: `application_command_failed`、`interaction_response_failed`、`schedule_create_failed`、`schedule_edit_failed`、`schedule_delete_failed`、`schedule_state_change_failed`

現在、WARNING専用の固定業務イベントは実装されていない。存在しない外部監視サービスを前提にせず、運用環境側で上記ERRORの発生、Recovery未完了、cycleの継続的欠落を監視する。

ログへtoken、DATABASE_URL、DB password、投稿本文、内部例外全文、tracebackを出さない。SQLAlchemyは`echo=False`、`hide_parameters=True`である。障害報告でも同じ原則を守る。

## 16. バックアップ

以下はPostgreSQL公式`pg_dump`を使う一般手順であり、このリポジトリ専用の自動バックアップ機能ではない。実施には対象環境の運用承認が必要である。

1. Botを正常停止し、バックアップ中の書込み方針を決める。
2. 接続先の環境名、host、port、DB名を安全な管理画面等で複数回確認する。
3. 秘密の接続情報をコマンド行へ直接書かず、安全な一時環境変数または`.pgpass`等で供給する。
4. 保存先を作り、所有者だけがアクセスできるようにする。

```bash
install -d -m 700 backups
backup_file="backups/discord_bot_$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_dump --dbname="$BACKUP_DATABASE_URL" --format=custom --file="$backup_file"
chmod 600 "$backup_file"
pg_restore --list "$backup_file"
sha256sum "$backup_file" > "$backup_file.sha256"
chmod 600 "$backup_file.sha256"
```

`BACKUP_DATABASE_URL`は安全な方法で事前設定し、値を表示しない。終了コード、`pg_restore --list`、ファイルサイズ、ハッシュを確認する。バックアップとハッシュをアクセス制御された別ストレージへ保管し、保持・暗号化・削除方針に従う。

## 17. 復元

> **警告:** 復元は破壊的になり得る。運用中DBへ直接上書きしない。必ず別の空DBを用意し、接続先を複数回確認してから承認済み手順で行う。

1. Botを復元先へ接続しない。
2. 復元先が本番DBでも開発DBでもない専用の空DBであることを確認する。
3. DB管理者が承認済みの方法で空DBを作成する。既存DBをdropしない。
4. 秘密情報をコマンド履歴へ書かず、復元先URLを安全に設定する。
5. custom形式を復元する。

```bash
pg_restore --dbname="$RESTORE_DATABASE_URL" --exit-on-error --no-owner "$BACKUP_FILE"
DATABASE_URL="$RESTORE_DATABASE_URL" python -m discord_ai_reminder_bot.infrastructure.database.migrate --target production --expected-database "$RESTORE_EXPECTED_DATABASE" current
```

6. Botを接続しないまま、テーブル件数と制約を読み取り専用で確認する。

```bash
psql "$RESTORE_DATABASE_URL" -c "SELECT 'schedules' AS table_name, count(*) FROM schedules UNION ALL SELECT 'schedule_runs', count(*) FROM schedule_runs UNION ALL SELECT 'delivery_attempts', count(*) FROM delivery_attempts UNION ALL SELECT 'operation_logs', count(*) FROM operation_logs UNION ALL SELECT 'notification_logs', count(*) FROM notification_logs UNION ALL SELECT 'notification_attempts', count(*) FROM notification_attempts;"
psql "$RESTORE_DATABASE_URL" -c "SELECT contype, count(*) FROM pg_constraint WHERE connamespace = 'public'::regnamespace GROUP BY contype ORDER BY contype;"
```

7. Migrationラッパーの`current`が期待headであること、テーブル件数がバックアップ記録と整合すること、CHECK・FK・UNIQUEが存在することを確認する。
8. 復元テストの結果を記録し、初めて運用上の切替判断へ進む。

`pg_restore --clean`、drop、truncate、stamp、手動DDLをこの一般手順へ追加しない。

## 18. テストDB

[READMEのPostgreSQL統合テスト](../README.md#postgresql統合テスト)に従う。`postgres_test`は`127.0.0.1:55432`の一時DBで、開発用`postgres`と名前付きVolumeを共有しない。

起動・停止・削除は必ずサービス名を明示する。

```bash
docker compose --profile test up -d postgres_test
docker compose --profile test ps
docker compose --profile test stop postgres_test
docker compose --profile test rm -f postgres_test
```

`TEST_DATABASE_URL`はコマンド単位で指定する。開発用`postgres`、`postgres_data`、本番DBへ触れない。プロジェクト全体やVolumeをまとめて削除するCompose操作を使わない。

### 18.1 予約ID Autocomplete確認

`/post show|edit|delete|pause|resume`の予約ID欄では、投稿先チャンネル名を基本の検索方法とする。`tester-a`、`tester`、`#tester-a`、`お知らせ`のように入力でき、完全一致・前方一致・部分一致（英字は大文字小文字を区別しない）で、利用者が閲覧・操作できる予約だけが最大25件表示される。UUID、種別、状態、数値channel ID完全一致も利用できる。`/post show`の通常候補には削除済み予約を表示しないが、完全なUUIDv7の直接入力と`/post list status:削除済み`からは削除済み詳細を参照できる。他コマンドの候補状態境界は変更しない。候補取得は読み取り専用で、channel名は設定guildのDiscordキャッシュだけを使用する。キャッシュにないchannelは名前検索できず、候補表示では短縮channel IDへ安全にフォールバックする。REST取得は行わない。

権限不足、DM、設定外guild、DB障害時は通常のephemeralエラーではなく空候補になる。候補取得後に状態が変化した場合は、コマンド実行時の既存の安全な拒否を確認する。Phase 3第1段階の実Discord受入項目は[Phase 3受入](manual-acceptance-phase3.md)に記録する。

### 18.2 予約詳細View基盤確認

`/post show`と一覧選択後の詳細は同じ表示DTOを使う。状態と操作可否に応じて編集・予約名編集・一時停止・再開・削除を表示し、一覧由来では「一覧へ戻る」も表示する。予約名編集は既存編集とは独立した1項目Modalで行い、空欄は名前解除、同値はno-opとする。draft、active、pausedだけを対象に、所有者・管理者とversionを再検証する。戻る操作は最新の権限とDB状態を読み、元の状態・種類・ページ条件を維持して、ページが減っていれば最後の有効ページへ補正する。

一覧・詳細ViewはBot稼働中にtimeoutしない。操作時は最新のguild、認可、所有者／管理者、Schedule状態、version、run、attemptを短いDB処理で再検証し、競合時は安全な案内と最新詳細へ更新する。Bot再起動後は古い画面を復元せず、`/post show`または`/post list`を再実行する。Bot停止時は一覧・詳細・既存確認Viewと開いているModalを停止し、wait Taskを回収する。削除済みephemeralメッセージはDiscordへ更新を試みず、Bot closeまたは画面遷移時にregistryから回収する。メッセージ削除や応答失敗だけでは業務状態を変更しない。詳細のversionと操作可否は内部情報であり、Embedや通常ログへ表示しない。

View、Button、SelectとModal内入力部品のcustom IDは固定値を維持する。外側Modalだけはdiscord.py 2.7.1のdispatch key衝突を避けるため、用途別固定prefixに32桁の非識別ランダムnonceを付ける。nonceへ予約・利用者・guild・channel・version・本文・名前・理由・秘密情報を含めない。別端末や別詳細で新しいModalを開いても既存Modalを停止せず、古いModalの有効期限内submitは最新認可・状態・version再検証へ進む。各Modalはsubmit、timeout、error時に自分だけを解除し、×で閉じたModalは有限timeout、残存する全ModalはBot正常停止時に回収する。timeout後の操作はCAS競合とは別の期限切れ動作として扱う。

名前Modalを開いた後に許可ロールまたは管理者権限を失った場合、古い画面の権限は再利用しない。最新詳細を安全に取得できなければ旧Viewを解除し、他人予約の情報を再表示せず、`現在の権限ではこの予約を編集できません。/post showを再実行してください。`だけを表示する。これは想定される認可境界であり、通常はERRORやtracebackを出さない。応答失敗時だけ固定イベントログを確認し、本文、予約名、UUID、内部version、例外全文が通常ログへ出ていないことを確認する。

### 18.3 詳細からの一時停止・再開・削除

- 詳細のボタンは表示時の状態を案内するが、操作時に認可、所有者、version、run、attemptを再検証する。
- 競合時は最新詳細を再取得し、内部versionや例外内容を表示・ログへ出さない。
- 再開4択と削除確認は15分で期限切れとなり、DBへアクセスせず部品をdisabledで残す。
- 管理者が同じguildの他作成者予約を削除する場合だけ、前後空白除去後1～500文字の理由を必須とし、空白だけを固定理由へ変換せず拒否する。有効理由は監査用OperationLogだけへ保存し、利用者向け応答、custom_id、通常ログへ全文を複製しない。管理者自身を含む作成者本人の削除では理由Modalを要求しない。
- 詳細の編集ボタンは単発・毎日・毎週ごとのModalを開き、投稿先、日時・繰り返し条件、本文を一度に更新する。本文と終了日は空欄で解除でき、従来の`/post edit`も引き続き利用できる。
- 詳細画面の「✏️ 編集」は画面を確認しながら編集する一般利用者向け、`/post edit`は予約IDを候補から選んで変更項目を1つ以上直接指定する短縮・上級者向けとする。一時停止中も既存仕様で許可された項目を直接編集できる。`local_time`は毎日・毎週の基本投稿時刻を恒久変更するもので、今回だけの時刻変更には使用しない。本文・終了日の明示解除には`clear_content`・`clear_end_date`を使う。
- 編集Modalは15分で期限切れとなる。Modalを閉じた場合や期限切れ時はDBを更新せず親詳細を維持し、再度編集できる。
- 予約名は最大32文字で、改行・制御文字を拒否する。保存名がなければ種別とJST日時からAIなしのフォールバック名を表示する。一覧、Select、Autocompleteには本文を表示せず、詳細の正式な本文欄だけを維持する。

## 19. 秘密情報の扱い

- `.env`はGit管理対象外とし、内容を表示しない。
- token、DATABASE_URL、passwordをコマンド例、ログ、Issue、証跡へ含めない。
- shell historyに本番資格情報を直接残さない。
- 投稿本文と本文プレビューを通知・通常ログへ含めない。
- 内部例外全文とtracebackを利用者応答・通常ログへ含めない。
- スクリーンショットはDiscord ID、UUIDv7、チャンネル名など運用情報の公開範囲を確認して保管する。
- 秘密漏えいが疑われる場合は、値を貼らずに対象credentialの失効・再発行手順へ移る。

## 20. 障害時に行ってはいけない操作

- 複数Botプロセスを場当たり的に同時起動する。
- unknownの投稿・通知を自動または無確認で再送する。
- `.env`、token、DATABASE_URL、投稿本文、例外全文を表示・共有する。
- Alembic CLI直接実行、offline mode、無承認の`stamp`、過去Revision書換え、手動DDL、`alembic_version`直接更新を行う。
- Schedule、Run、Attemptの状態を手動SQLで修復する。
- 運用中DBへバックアップを直接restoreする。
- 接続先未確認でdrop、truncate、`pg_restore --clean`を行う。
- プロジェクト全体やVolumeをまとめて削除するCompose操作で開発データを巻き込む。
- cleanup用のCLIは存在しないため、管理コマンドを捏造する。
- Rate Limitやunknownを本物のDiscordへの大量送信で意図的に再現する。

## 21. Phase 3と一般公開の運用方針（未実装）

Phase 3と一般公開の詳細は[開発・公開ロードマップ](development-roadmap.md)を正本とする。現在はローカル開発だけを対象とし、常時稼働サービス、AI Provider実行、顧客Quota、`failed`・`draft`・`paused`の新しい整理処理は未実装である。存在しない起動、課金、cleanupコマンドを本Runbookへ追加しない。

正式リリースではサブスクリプション契約を導入するが、現段階では決済機能、料金プラン、契約テーブル、Webhook、課金用コマンドは存在しない。ローカル開発・ポートフォリオ段階では課金せず、公開前限定テストでは選定した決済Providerのsandbox／test modeだけを使用する。PAY.JPは候補であり正式採用済みではない。カード情報、決済秘密情報、Webhook本文、利用者情報を通常ログへ出さない。

50回／日、500回／月、100円相当／月はProvider未選定・未接続の2B初期実装・隔離テスト用の変更可能な安全設定値であり、販売価格や正式リリース時の恒久上限ではない。2Cと商品仕様策定時に実単価、利用量、収益、インフラ・保存・監視・決済等の原価、予備費、実用上必要な品質から再計算する。正式リリース後の必要な運用費はサブスクリプション収益で賄い、通常利用が困難になるほど低い上限や0円運用を運用要件にしない。運営Budgetと顧客Quotaを分離し、利用状況、原価、解約率、障害率に応じて見直す。

2B／2C-1の設定名は`.env.example`を参照する。初期状態は`AI_NAME_GENERATION_ENABLED=false`かつ`AI_NAME_GENERATION_PROVIDER=disabled`で、APIキーも設定しないため外部AI通信や費用は発生しない。有効フラグだけではOpenAI Adapterを構成せず、Provider、許可モデル、秘密キー、監査済み単価、為替、入出力上限、安全係数が揃った場合だけ利用可能になる。設定無効またはGenerator unavailableならJob登録もpoll task作成も行わない。設定値、本文、生成名、ID、Provider request ID、例外全文を通常ログへ列挙しない。

実Provider受入は通常pytest、CI、Bot通常起動から分離し、利用者の明示許可後だけ行う。専用OpenAI Project、制限付きAPIキー、Project予算・アラート、最大呼出回数・最大費用を事前確認し、`gpt-5.6-luna`と`gpt-5.4-nano-2026-03-17`へ同じ固定匿名ケースを各ケース1回ずつ送る。日本語品質、32文字、応答時間、usage、timeout、cancel、請求を比較し、本文・生成名を通常テストログへ出さない。APIキーをcommand引数、Git、`.env.example`へ記載しない。実施前にモデル提供状態、単価、Luna alias、標準保持最大30日、ZDR、国内処理を公式資料で再監査する。

2C-2の手動CLIは、次のdry-runだけを通常作業で実行できる。引数なしと`--help`も通信せず、dry-runはキーの有無、長さ、prefix、末尾、hashを表示しない。

```bash
python -m discord_ai_reminder_bot.infrastructure.ai.acceptance --dry-run
```

live実行はこのRunbookを見ただけでは許可されない。利用者の明示許可に加え、専用Project、制限付きキー、Provider予算・アラート、公式endpoint、モデル・単価・保持・請求条件を再確認し、dry-runが表示するProvider、モデル一覧、最大request数、最大JPY microunits、`live`操作を束縛したconfirmationを完全一致で指定する。専用キーは`OPENAI_PROVIDER_ACCEPTANCE_API_KEY`として、その1 processの環境へ秘密管理機構から渡し、`.env`、shell history、CLI引数、ファイルへ書かない。具体的live commandは許可時にdry-run出力から組み立て、通常pytest、CI、Bot起動手順へ追加しない。

CLIは固定6件の匿名合成caseだけを各選択modelへ直列送信し、request開始前に回数と悲観費用を消費する。process内上限は単一runの誤超過防止であり複数process間の排他ではないため、同じProjectで手動受入CLIを同時起動してはいけない。専用Project予算を全processの最終安全境界とし、現段階ではDB lockやOS固有lockを追加しない。失敗、timeout、cancel時は残りを実行せず、clientを回収する。生成名はlive端末へ一度だけ表示し、自動保存しない。ただし実行者によるshell redirect、terminal scrollback、画面収録までは技術的に防げないため、保存する場合も合成データだけの受入証跡として管理する。Provider request ID、raw usage、raw request／response、APIキー、例外全文を記録しない。表示するJPY microunitsは受入用悲観費用であり販売価格ではない。

実Provider受入前はどちらのモデルも正式採用済みとせず、ProviderとAIを無効のまま維持する。AI枠超過、Provider障害、AI無効でも予約作成・投稿、JSTフォールバック名、手動名編集を継続する。将来のプラン別モデル・回数・機能は商品仕様で決め、2C-1の運営Budgetへ顧客Quotaを混ぜない。

実Provider受入は公開前まで延期する。これは中止や不要判定ではない。専用Projectは作成済みで、比較候補`gpt-5.6-luna`と`gpt-5.4-nano`だけを許可し、各モデル60,000 TPM・10 RPMとしているが、Project名・ID等の内部識別情報を文書や通常ログへ残さない。現在は残高0 USD、支払い方法未登録、APIキー未作成、API通信0回、費用発生なしであり、この状態ではliveを実行しない。Project作成だけでOpenAIを正式採用済みとは扱わない。

課金可能な状態を作る前に利用者の明示許可を再取得する。最低プリペイド購入が必要になる可能性がある場合はAuto-rechargeを無効にし、購入額を実試験そのものの費用と記録しない。専用Project、制限付きキー、Project予算・アラートを確認後、Lunaの固定匿名6 caseを実行し、終了後に別runとして固定`gpt-5.4-nano-2026-03-17`の同じ6 caseを実行する。両runとも各requestの間隔を60秒以上空ける。合計12 requestで、retry、fallback、Batch、並列実行は行わない。悲観費用はLuna 333,600 JPY microunits／回、GPT-5.4 nano 334,200 JPY microunits／回、合計4,006,800 JPY microunits（約4.0068円）であり、プリペイド購入額や販売価格とは別である。結果をDBやファイルへ自動保存せず、日本語品質、32文字、応答時間、token、請求、保持、dashboard設定を確認する。

OpenAI SDK依存は`pyproject.toml`の2.54 minor範囲を正式`.venv`へ通常の`python -m pip install -e '.[dev]'`で解決する。本リポジトリはlock fileを管理していないため新規作成せず、SDK patch更新時は実APIへ接続しないMock transport contract testを必須にする。AdapterにはPython 3.14／WSL2での初回platform検出停止を避けるため、SDK 2.54.xだけを対象とした非公開のinstance-local platform cache初期化がある。これは公式に保証された公開APIではないため、未知version、private symbolまたは型の変更時は外部通信前にfail-closedとし、無通信contract testが成功するまでProviderを有効化しない。module symbolのmonkey patchやprocess-wide global変更は認めず、SDKが公開手段を提供した時点で撤去を検討する。package metadataのOS-independent表記だけをARM64実機受入の代替にせず、配置architecture確定後に同等ARM64 Linux上で依存解決、import、Mock transport、shutdownを確認する。

将来のAI機能は初期状態で無効とし、明示的に有効化した場合も固定イベントと安全な集計だけを監視する。APIキー、本文、AI入力全文、AI応答全文を通常ログへ記録しない。ログは14日、DBバックアップは日次7世代かつ最大14日保持する方針とし、配置環境決定時に自動削除、アクセス制御、復元後cleanupの具体手順を確定する。

公開前限定テストでは開発環境と分離したDiscord Application、DB、秘密情報を使い、実Discord、権限差、複数利用者競合、長時間稼働、再起動・Recovery、バックアップを確認する。Phase 3受入表はPhase 3実装開始時に別文書として新規作成し、Phase 1・Phase 2の完了証跡へ項目を追加しない。

## 22. ポートフォリオassetの作業境界

Phase 3第6項の詳細は[ポートフォリオ掲載計画](portfolio-plan.md)を正本とする。6Bの画面資料は専用開発guildの合成データで新規撮影し、既存の実Discord受入画像、実利用者本文、実IDを流用しない。撮影前に架空のBot名、利用者名、予約名、本文、channel名を用意し、サイドバー、DM、通知、端末情報、実UUIDが写らない範囲を選ぶ。

公開用assetは復元不能な匿名化とmetadata除去後に再確認し、元画像や編集レイヤーをGitへ含めない。`docs/portfolio/assets/manifest.md`へ合成データ、匿名化方法、確認日、確認者、掲載先だけを記録し、実guild名、Project ID、Organization ID、Discord ID、ローカルパスを記録しない。READMEへは代表画像だけを掲載し、完成前のassetを参照しない。

READMEの再現手順は安全な最短入口に限定し、DB操作、Discord設定、障害対応は本Runbookへリンクする。AI Providerのlive confirmation、APIキー、課金手順をREADMEへ掲載せず、dry-runだけを通常の確認入口とする。6Cでは公開前Git履歴の秘密情報、ライセンス、依存ライセンス、第三者素材、Discord／OpenAIの商標・画面条件を確認する。
