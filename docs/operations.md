# Phase 1 運用Runbook

## 1. 文書の目的と対象環境

本書は、Phase 1のDiscord予約投稿Botを開発・運用し、障害時に安全に判断するためのRunbookである。対象は環境変数で指定した1つのDiscord guild、WSL2またはLinux上の通常版CPython 3.14、Docker Compose上のPostgreSQL 18.4である。

コマンドは特記がない限りリポジトリルートで実行する。開発DB、テストDB、本番DBを取り違えないことを最優先とし、実行前に接続先を確認する。予約の運用識別子には内部DB IDではなく完全なpublic UUIDv7を使う。

基本セットアップは[README](../README.md)、仕様は[要件書](requirements-beta.md)と[技術設計書](technical-design-beta.md)、実Discord確認は[手動受入チェックリスト](manual-acceptance-phase1.md)を参照する。

`/post list` は全件数・総ページ数を表示し、前後ボタン、予約種類（すべて・単発・毎日・毎週）の絞り込み、現在ページの予約選択で詳細を確認できる。種類を変えると状態条件を保ったまま1ページへ戻り、0件でも別種類へ切り替えられる。Viewは最後の操作から15分間操作できる。期限切れ後も一覧、フィルター、ページと操作部品は画面に残るが、全部品がdisabledになり、最新一覧は `/post list` を再実行するよう案内される。操作のたびに最新状態と権限を確認するため、操作間の作成・変更・削除により表示位置が変わることは正常である。終了日は `明日`、`8/30`、`2026-08-30` などを指定でき、成功表示では常に完全な `YYYY-MM-DD` となる。

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
alembic upgrade head
alembic current
alembic heads
alembic check
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
alembic current
alembic heads
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

1. Botを停止したままにする。
2. `.env`を表示せず、対象環境と接続先が正しいか別の安全な管理手段で確認する。
3. 本番相当環境なら先に[バックアップ](#16-バックアップ)を取得する。
4. 状態を読み取る。

```bash
alembic current
alembic history
alembic heads
alembic check
```

5. Revision鎖と変更手順をレビューする。
6. 承認された接続先にだけ適用する。

```bash
alembic upgrade head
alembic current
alembic check
```

7. Botを再起動する。

安易な`alembic stamp`、過去Revisionの書換え、手動DDL、`alembic_version`の直接更新を行わない。

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
DATABASE_URL="$RESTORE_DATABASE_URL" alembic current
```

6. Botを接続しないまま、テーブル件数と制約を読み取り専用で確認する。

```bash
psql "$RESTORE_DATABASE_URL" -c "SELECT 'schedules' AS table_name, count(*) FROM schedules UNION ALL SELECT 'schedule_runs', count(*) FROM schedule_runs UNION ALL SELECT 'delivery_attempts', count(*) FROM delivery_attempts UNION ALL SELECT 'operation_logs', count(*) FROM operation_logs UNION ALL SELECT 'notification_logs', count(*) FROM notification_logs UNION ALL SELECT 'notification_attempts', count(*) FROM notification_attempts;"
psql "$RESTORE_DATABASE_URL" -c "SELECT contype, count(*) FROM pg_constraint WHERE connamespace = 'public'::regnamespace GROUP BY contype ORDER BY contype;"
```

7. `alembic current`が期待headであること、テーブル件数がバックアップ記録と整合すること、CHECK・FK・UNIQUEが存在することを確認する。
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

`/post show|edit|delete|pause|resume`の予約ID欄では、投稿先チャンネル名を基本の検索方法とする。`tester-a`、`tester`、`#tester-a`、`お知らせ`のように入力でき、完全一致・前方一致・部分一致（英字は大文字小文字を区別しない）で、利用者が閲覧・操作できる予約だけが最大25件表示される。UUID、種別、状態、数値channel ID完全一致も利用でき、候補が出ない場合も完全なUUIDv7を直接入力できる。候補取得は読み取り専用で、channel名は設定guildのDiscordキャッシュだけを使用する。キャッシュにないchannelは名前検索できず、候補表示では短縮channel IDへ安全にフォールバックする。REST取得は行わない。

権限不足、DM、設定外guild、DB障害時は通常のephemeralエラーではなく空候補になる。候補取得後に状態が変化した場合は、コマンド実行時の既存の安全な拒否を確認する。実Discord受入項目は[Phase 2手動受入](manual-acceptance-phase2.md)に記録する。

### 18.2 予約詳細View基盤確認

`/post show`と一覧選択後の詳細は同じ表示DTOを使う。第1段階では編集・一時停止・再開・削除ボタンをまだ表示せず、一覧由来だけ「一覧へ戻る」を表示する。戻る操作は最新の権限とDB状態を読み、元の状態・種類・ページ条件を維持して、ページが減っていれば最後の有効ページへ補正する。

一覧由来の詳細Viewは最後の操作から15分で期限切れとなる。期限切れ時はDBへ接続せず、詳細Embedとdisabledの戻るボタンを残し、`/post show`または`/post list`による再確認を固定文で案内する。Bot停止時は一覧・詳細・既存確認Viewと開いているModalを停止・回収する。詳細のversionと操作可否は内部情報であり、Embedや通常ログへ表示しない。

### 18.3 詳細からの一時停止・再開・削除

- 詳細のボタンは表示時の状態を案内するが、操作時に認可、所有者、version、run、attemptを再検証する。
- 競合時は最新詳細を再取得し、内部versionや例外内容を表示・ログへ出さない。
- 再開4択と削除確認は15分で期限切れとなり、DBへアクセスせず部品をdisabledで残す。
- 管理者による他人の削除理由は監査ログへ保存するが、custom_idや通常ログへ含めない。
- 詳細からの編集は未実装であり、`/post edit`を使用する。

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
- `alembic stamp`、過去Revision書換え、手動DDL、`alembic_version`直接更新を行う。
- Schedule、Run、Attemptの状態を手動SQLで修復する。
- 運用中DBへバックアップを直接restoreする。
- 接続先未確認でdrop、truncate、`pg_restore --clean`を行う。
- プロジェクト全体やVolumeをまとめて削除するCompose操作で開発データを巻き込む。
- cleanup用のCLIは存在しないため、管理コマンドを捏造する。
- Rate Limitやunknownを本物のDiscordへの大量送信で意図的に再現する。
