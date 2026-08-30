# Phase 1 β版 技術設計

## 1. 設計目的と上位要件

本書は、[Phase 1 β版 要件定義](requirements-beta.md)を実装へ落とし込むための技術設計書である。要件定義を上位文書とし、解釈が分かれる箇所では本書の採用案に従う。ただし、本書と要件定義が矛盾した場合は要件定義を優先し、実装前に本書を修正する。

Phase 1では、環境変数で指定した1つのDiscordサーバーに対して、単発、毎日、毎週の予約投稿を安全に実行する。予約、各回の実行状態、通知結果をPostgreSQLへ保存し、Botを再起動しても処理を復旧できるようにする。

### 1.1 設計方針

- 対応言語は日本語、利用者向けタイムゾーンは `Asia/Tokyo` に固定する。
- PostgreSQLを予約状態の唯一の正しい保存先とする。
- Discord Bot、業務処理、予約ルール、DB・Discord接続を分離する。
- Discord APIやDBへ依存しない予約ルールを先に実装し、単体テスト可能にする。
- 非同期I/Oを使用し、Discord Botの応答を同期処理で止めない。
- DBポーリング方式を採用し、APSchedulerとCeleryはPhase 1では使用しない。
- PostgreSQLだけをDocker Composeで起動し、BotはWSL上の `.venv` で実行する。
- 初期実装ではファイルを過度に細分化せず、責務が大きくなった時点で分割する。
- Phase 2のAI、文体、PAY.JP、複数サーバー対応を追加できる境界を設ける。
- 自動再送による二重投稿を避けることを、投稿の取りこぼしを避けることより優先する。

## 2. システム構成

Phase 1は、次の2つの実行要素で構成する。

```text
WSL
┌────────────────────────────────────────────────────┐
│ CPython 3.14 / .venv                               │
│                                                    │
│ Discord Botプロセス                                │
│ ├─ スラッシュコマンド                              │
│ ├─ アプリケーションサービス                        │
│ ├─ DBポーリングワーカー                            │
│ ├─ 起動時復旧                                      │
│ ├─ 下書き通知・運営者通知                          │
│ └─ 30日経過データの削除                            │
└───────────────┬───────────────────┬────────────────┘
                │                   │
                │ 非同期DB接続      │ HTTPS / Gateway
                ▼                   ▼
Docker Compose                  Discord API
┌──────────────────┐
│ PostgreSQL       │
│ ├─ 予約          │
│ ├─ 実行履歴      │
│ ├─ 送信試行      │
│ ├─ 操作履歴      │
│ └─ 通知履歴      │
└──────────────────┘
```

Botプロセス内の各バックグラウンド処理は同じアプリケーションサービスを呼び出す。スラッシュコマンド、通常ポーリング、起動時復旧で業務ルールを重複実装しない。

Phase 1では1つのBotプロセスを標準運用とする。ただし、誤って複数起動した場合も二重実行しないDB排他制御を実装する。

## 3. 採用技術と選定理由

| 分類 | 採用技術 | 選定理由 |
| --- | --- | --- |
| Python | 通常版CPython 3.14 | READMEの開発環境と統一し、型や標準ライブラリを新しい安定版へ揃えるため |
| Discord | discord.py 2.x | スラッシュコマンド、Cog、権限、DM、非同期処理に対応するため |
| 設定 | pydantic-settings | 必須値、数値ID、環境ごとの差を起動時に検証するため |
| ORM | SQLAlchemy 2.x | 型付きORM、非同期接続、PostgreSQL機能、将来拡張に対応するため |
| DBドライバー | Psycopg 3 | PostgreSQL向けでasyncioに対応し、SQLAlchemyから利用できるため |
| マイグレーション | Alembic | SQLAlchemyモデルとDBスキーマの変更履歴を管理するため |
| DB | PostgreSQL | 排他ロック、制約、トランザクション、将来の複数プロセス化に対応するため |
| 定期ループ | discord.ext.tasks | Botのasyncioループ上で、ポーリングと再接続を扱うため |
| テスト | pytest、pytest-asyncio | 同期・非同期の単体テストと統合テストを同じ形式で記述するため |
| 品質 | Ruff | lintとformatを1つのツールで管理するため |
| コンテナ | Docker Compose | 開発用PostgreSQLだけを再現可能に起動するため |
| ログ | Python標準logging | 依存を増やさず、構造化ログへ拡張できるため |

### 3.1 Python設定の変更方針

実装開始時に `pyproject.toml` の次の変更が必要である。本書作成時点では変更しない。

```toml
[project]
requires-python = ">=3.14,<3.15"

[tool.ruff]
target-version = "py314"
```

開発・テスト・本番で同じPython 3.14系を使用する。自由スレッド版ではなく、通常版CPythonを使用する。依存ライブラリを追加する前に、採用バージョンがPython 3.14へ対応していることを確認する。

## 4. 責務分離

### 4.1 Bot層

Bot層はDiscordとの入出力だけを担当する。

- スラッシュコマンドの登録
- Discord入力値の受取と基本的な形式確認
- 対象サーバー、管理者、許可ロール、予約所有者の確認
- 本人だけに見える応答、確認ボタン、一覧のページング
- アプリケーションサービスの呼び出し
- 業務エラーを利用者向け日本語メッセージへ変換

Bot層はSQLAlchemyモデルを直接操作せず、次回日時や状態遷移を独自に計算しない。

`/post create`、`/post list`、`/post show` の成功表示はBot層の共通presenterで1つのDiscord Embedへ変換する。タイトル256文字、description 4,096文字、Field名256文字、Field値1,024文字、Field数25、Embed合計6,000文字を上限とし、利用者入力由来の本文はメンションとMarkdownを無効化する。予約IDは省略せずインラインコードで表示し、状態は日本語名とアイコンを色に加えて示す。

予約一覧は `/post list status:<任意> page:<1以上、既定1>` とし、同一のguild・作成者/管理者・status・任意schedule_type条件でCOUNTと1ページ10件を取得する。不変DTOだけをBotへ返し、`next_run_at ASC NULLS LAST, id ASC`を維持する。本人限定でBot稼働中はtimeoutしないViewは前後ボタン、固定値`all/once/daily/weekly`の種類Select、最大10件のUUIDv7詳細Selectを別custom_id・別Action Rowで持ち、詳細は既存Presenterを再利用する。種類変更ではpageを1へ戻し、空結果でも種類Selectを残す。ページ移動と詳細からの復帰ではstatus・schedule_type・pageを維持する。各操作は短い新規read Sessionと再認可で最新状態を取得する。Session、transaction、row lockは待機中に保持しない。`asyncio.Lock`と終了状態でSelectを含む多重操作を直列化する。Bot closeではViewをstopしてViewStore registryから除去しwaitを回収する。操作時は消滅した末尾ページを補正し、コマンドで明示した巨大pageの安全な空結果は維持する。状態未指定時は `deleted` を除外し、明示指定時だけ含める。再起動時のpersistent View登録・復元は行わない。

### 4.2 アプリケーション層

アプリケーション層は、1つの利用目的を完了させる処理を担当する。

- 予約の作成、一覧、詳細、編集、削除
- 定期投稿の一時停止、再開
- 実行予定の生成と処理権取得
- Discord投稿、再試行、結果保存
- 起動時復旧
- 下書き通知、運営者通知
- 期限切れデータの削除
- 操作履歴と通知履歴の保存

Sessionとtransactionはorchestration boundaryが所有する。Botコマンドとruntimeに加え、`PollingWorker`、`NotificationWorker`、`CleanupService`など処理を編成するApplication層のオーケストレーターも、必要な短いtransactionを構成できる。Repositoryと業務Domain/Application Serviceはcommitまたはrollbackしない。

### 4.3 ドメイン層

ドメイン層は、外部サービスに依存しない業務ルールを担当する。

- 予約種別と状態
- 許可される状態遷移
- 単発、毎日、毎週の次回日時計算
- 終了日の判定
- 5分前の編集制限
- 15分以内の遅延投稿判定
- 最大4回の試行と再試行時刻
- 本文とメンションの検証

ドメイン層はDiscordオブジェクト、SQLAlchemy Session、環境変数を参照しない。

### 4.4 インフラストラクチャ層

インフラストラクチャ層は外部技術との接続を担当する。

- SQLAlchemyモデルとRepository実装
- PostgreSQL接続とトランザクション
- Discordへの投稿とDM
- `allowed_mentions` の安全な生成
- ログ出力

Discord投稿はインターフェース越しに呼び出し、テストでは偽物へ交換できるようにする。

## 5. 採用するディレクトリ構成

初期実装では次の構成を採用する。

```text
discord-ai-reminder-bot/
├── alembic/
│   ├── versions/
│   └── env.py
├── docs/
│   ├── requirements-beta.md
│   └── technical-design-beta.md
├── src/
│   └── discord_ai_reminder_bot/
│       ├── __init__.py
│       ├── __main__.py
│       ├── config.py
│       ├── log_config.py
│       ├── bot/
│       │   ├── client.py
│       │   └── posts.py
│       ├── domain/
│       │   ├── schedules.py
│       │   └── recurrence.py
│       ├── application/
│       │   ├── schedules.py
│       │   ├── execution.py
│       │   └── maintenance.py
│       └── infrastructure/
│           ├── database.py
│           ├── repositories.py
│           └── discord_gateway.py
├── tests/
│   ├── unit/
│   │   ├── test_recurrence.py
│   │   ├── test_schedule_states.py
│   │   ├── test_permissions.py
│   │   └── test_retry_policy.py
│   ├── integration/
│   │   ├── test_repositories.py
│   │   ├── test_run_claiming.py
│   │   └── test_recovery.py
│   ├── bot/
│   │   └── test_post_commands.py
│   ├── conftest.py
│   └── test_package.py
├── .env.example
├── alembic.ini
├── compose.yaml
├── pyproject.toml
└── README.md
```

初期段階ではDBモデル、接続、セッションを `infrastructure/database.py` にまとめる。ファイルが読みにくくなった場合に `infrastructure/database/` パッケージへ分割する。同様にBotの共通チェックやエラー処理も、複数コマンドで必要になった時点で分離する。

## 6. DBテーブル設計

### 6.1 共通方針

- 各テーブルの内部主キーはPostgreSQLの `BIGINT GENERATED ALWAYS AS IDENTITY` とする。
- 予約の公開識別子 `public_id` は、Python 3.14標準の `uuid.uuid7()` でアプリケーション側が生成するUUIDとする。
- 内部主キーはテーブル結合と外部キー参照だけに使い、Discordの応答、コマンド入力、ログの利用者向け表示には公開しない。
- DiscordのSnowflake IDはPostgreSQLの `BIGINT` で保存する。
- 日時は `TIMESTAMP WITH TIME ZONE` でUTCとして保存する。
- 作成・更新日時はDB側の現在時刻を初期値とする。
- 状態値は `VARCHAR` とCHECK制約で管理し、PostgreSQL固有Enumは使用しない。
- 本文はDiscordのPhase 1上限に合わせて最大2,000文字とする。
- 外部キー削除は原則 `RESTRICT` とし、30日後の削除処理が関連行から順に明示的に削除する。
- 業務上の更新競合を検出するため、`schedules` に `version` を持たせる。

### 6.2 `schedules`：予約全体

| カラム | 型 | NULL | 説明 |
| --- | --- | --- | --- |
| `id` | BIGINT IDENTITY | 不可 | 内部主キー。DB内の結合だけに使用し、外部へ公開しない |
| `public_id` | UUID | 不可 | Pythonの `uuid.uuid7()` で生成し、Discord上で予約を指定する公開識別子として使用 |
| `guild_id` | BIGINT | 不可 | DiscordサーバーID |
| `channel_id` | BIGINT | 不可 | 投稿先チャンネルID |
| `creator_user_id` | BIGINT | 不可 | 作成者のDiscordユーザーID |
| `schedule_type` | VARCHAR(16) | 不可 | `once`、`daily`、`weekly` |
| `status` | VARCHAR(16) | 不可 | `draft`、`active`、`paused`、`failed`、`completed`、`ended`、`deleted` |
| `content` | VARCHAR(2000) | 可 | 投稿本文。下書き、一時停止中、または本文なしの状態から削除された予約はNULL可。削除時は削除前の値を保持し、空文字やダミー本文は保存しない |
| `next_run_at` | TIMESTAMPTZ | 可 | 次回予定日時。実行対象がない状態ではNULL |
| `local_time` | TIME | 可 | 毎日・毎週の日本時間。単発はNULL |
| `weekday` | SMALLINT | 可 | 毎週の曜日。月曜0から日曜6。毎週以外はNULL |
| `end_date` | DATE | 可 | `Asia/Tokyo` 基準の終了日 |
| `version` | INTEGER | 不可 | 楽観ロック用。初期値1 |
| `created_at` | TIMESTAMPTZ | 不可 | 作成日時 |
| `updated_at` | TIMESTAMPTZ | 不可 | 更新日時 |
| `deleted_at` | TIMESTAMPTZ | 可 | 削除日時 |
| `terminal_at` | TIMESTAMPTZ | 可 | `completed`、`ended`、`deleted` になった日時 |

制約:

- 主キー: `id`
- `public_id` は `UUID NOT NULL UNIQUE`
- `schedule_type` は `once`、`daily`、`weekly` のいずれか
- `status` は定義済み予約状態のいずれか
- `content` はNULLまたは1～2,000文字
- `draft` の `content` はNULL
- `paused` の `content` はNULLまたは本文あり
- `deleted` の `content` は削除前の値を保持するためNULLまたは本文あり
- `active`、`failed`、`completed`、`ended` の `content` は必須
- `daily` は `local_time` 必須、`weekday` はNULL
- `weekly` は `local_time` と `weekday` 必須
- `once` は `local_time` と `weekday` がNULL
- `weekday` は0～6
- `end_date` は定期投稿だけ設定可能
- `completed` は単発だけ、`ended` と `paused` は定期投稿だけ
- `completed`、`ended`、`deleted` では `terminal_at` 必須

主要インデックス:

- `UNIQUE (public_id)`
- `(guild_id, status, next_run_at)`：一覧と実行対象検索
- `(creator_user_id, status, next_run_at)`：作成者別一覧
- `(status, terminal_at)`：30日後削除

`id` と `public_id` の役割は明確に分ける。`id` はDB内部で効率よく外部キーを結ぶための連番主キーであり、`public_id` はDiscord利用者が `/post show`、`/post edit`、`/post delete` などで予約を指定するための公開識別子である。外部へ内部の連番主キーを公開してはならない。

重複予約は警告に留め、DBの一意制約では禁止しない。利用者が意図的に同じ投稿を複数作る場合があるためである。

単発作成の重複候補は、同一サーバー、投稿先、予定日時、本文（NULL同士を含む）、`once`、かつ状態が `draft`、`active`、`paused` の予約とする。`allow_duplicate=false` では保存せず、trueなら作成する。この確認は誤操作防止であり、同時実行を直列化するロックや一意制約は設けないため、完全な重複防止は保証しない。作成時はDB不要の検証後に、完全なJST日時を固定したephemeral確認Embedと緑色の予約・灰色のキャンセルボタンを持つ120秒の非永続Viewを表示する。本人だけが操作でき、操作はView単位のlockで直列化する。確認中はDB Session、トランザクション、行ロックを保持しない。予約ボタン時にguildと共通認可、TextChannelとBotのview/send権限、本文、固定済み日時の5分境界を最新状態で再検証し、その後だけ新しい1つのトランザクションでSchedule、最初のpending ScheduleRun、必要なdraft通知計画を保存する。年や日付はボタン時に再推論しない。キャンセル、timeout、再検証失敗、重複警告では保存せずViewを終了する。autocompleteとカレンダーUIは対象外とする。

定期作成は既存の `/post create` を変更せず、毎日を `/post create-daily`、毎週を `/post create-weekly` とする。終了日Domain parserはClockのUTC aware datetimeをJSTへ変換し、`今日`、`明日`、`明後日`、`M/D`、`YYYY/M/D`、厳密な`YYYY-MM-DD`から純粋な`date`を返す。限定的な前後・Unicode空白正規化だけを行い、NFKCは使わない。半角違反と一般形式不正は型付き例外でBot案内を分け、DB開始前に拒否する。正規化したdateを既存Application検証と重複条件へ渡し、入力文字列は保存しない。

定期作成でもDB不要の検証後にephemeralでdeferし、呼び出し側のorchestration boundaryが所有する1トランザクションでScheduleと最初のpending ScheduleRunを保存する。`Schedule.next_run_at`、`ScheduleRun.scheduled_for`、`ScheduleRun.next_attempt_at`には同じUTC日時を設定する。draftでは必要な事前通知NotificationLogも同じトランザクションで計画する。Repositoryと業務Application Serviceはcommitまたはrollbackせず、トランザクション中にDiscord APIを呼ばない。

編集は単一の `/post edit` とし、`public_id`を必須、`channel`、`scheduled_at`、
`local_time`、`weekday`、`end_date`、`content`、`clear_content`、`clear_end_date`を任意とする。
予約IDをAutocomplete候補から選び、1項目以上の変更を直接指定する短縮・上級者向け経路であり、画面を確認しながら操作する一般利用者向けの詳細画面「✏️ 編集」と使い分ける。`local_time`は毎日・毎週予約の基本投稿時刻を恒久的に変更する指定であり、今回だけの投稿時刻変更には使用しない。一時停止中も既存仕様で許可された項目を直接編集できる。
単発、毎日、毎週で許可する項目は要件定義どおりに検証し、予約種別、guild、作成者、
public_idは不変とする。confirm、View、Modalは使わず、ローカル検証後にephemeralでdeferし、
コマンド所有のトランザクションをcommitした後で成功Embedをfollowupする。変更指定なし、
clear=falseだけ、排他的な値とclearの同時指定、実値が変わらないno-opでは更新と履歴追加をしない。
変更指定なしは固定の専用案内を`AllowedMentions.none()`付きephemeral応答で返し、Session、transaction、row lock、Application Service、OperationLogへ到達させない。明示した値が現在値と同じ場合は、従来どおりService内のno-opとして扱う。不正日時、不正終了日、不正本文、排他違反は既存の原因別案内を維持する。

編集可能状態は`draft`、`active`、および定期の`paused`だけとする。`draft`と`active`は
現在の`next_run_at >= edited_at + 5分`を必要とし、新しい単発日時または定期候補も同じ
包含境界を満たす。本文・channelだけならpendingと再試行待ちpendingを維持する。
日時・繰り返し設定が実際に変わり新候補が現在日時と異なる場合、全pendingをDeliveryAttemptを
追加せず`skipped`、`result_code = 'schedule_edited'`として新しいattempt_count=0のpendingを作る。
既存DeliveryAttemptと終端runは変更しない。

定期の新候補は`edited_at + 5分`以降の最初の発生から求める。候補が現在pendingと同日時なら
維持し、それ以外の既存runと同日時なら、最大でも既存run件数+1回の候補評価で次の発生へ進む。
終了日を超えれば次回なしとする。本文ありactiveは`ended`、本文なしdraftは編集全体を拒否する。
pausedはNULLの`next_run_at`を維持する。未来の健全な通常初回runはpendingのまま保持できる。channel・本文編集では保持し、local_time・weekday・end_date変更では旧保持runを`skipped(schedule_edited)`として、再開時に編集後設定を使う。
retry待ちpendingも編集可能で、本文・channel変更は維持、日時・recurrence変更はskipする。

編集トランザクションはロックなしでScheduleの内部参照、version、next_run_atを得て、全runを
ID昇順で`FOR UPDATE`して必要なDeliveryAttempt段階を確認し、その後Scheduleを`FOR UPDATE`する。
guild、public_id、作成者、version、状態、next_run_at、run状態を再検証し、processing、claimed、
sending、送信成功後のSchedule確定待ちは無変更で拒否する。run、Schedule、`action='edited'`の
OperationLogを同一トランザクションで変更する。本文は`content_changed: true`だけを記録し、
channel、日時、曜日、終了日、状態、skip件数、次回再計算の有無だけをchangesへ保存できる。
内部ID、public_id、version、本文、Discord message ID、例外全文は複製しない。

削除は `/post delete public_id:<canonical UUIDv7> reason:<任意、最大500文字>` とする。作成者本人（管理者を含む）の理由は任意で、前後空白除去後に未指定または空白だけならOperationLogへ`理由未入力`を保存し、表示は`未入力`とする。管理者が同じguildの他作成者予約を削除する場合だけ`DeleteReasonModal`を先に表示し、前後空白を除去した1～500文字を必須とする。空文字、Unicode・制御空白だけ、501文字以上は型付き入力エラーで拒否し、固定理由へ変換しない。Discord TextInputも`required=True`、`max_length=500`とし、Modal入力境界ではSession開始前、Application Serviceでは所有者判定後かつSchedule lock後にも同じ規則を最終防御として適用する。有効理由はOperationLogだけへ監査用に保存し、確認・成功応答は入力済みの固定表示として、利用者向け応答、custom_id、通常ログへ理由全文を複製しない。不正ID、不存在、権限不足、状態不許可、処理中は同じ固定応答とする。

コマンド実行時は読み取り専用で対象、所有者、状態、runを確認し、ephemeral Embedと赤色の削除・灰色のキャンセルボタンを持つ非永続Viewを表示する。Viewのtimeoutは120秒で、起動時のpersistent View登録は行わず、custom_idへ予約・利用者データを含めない。確認を開いた本人だけが操作でき、ボタン時にもguildと共通認可を再確認する。同一Viewの操作はロックで直列化し、成功、キャンセル、timeout後はViewを除去する。View待機中はDB Sessionやトランザクションを保持せず、削除ボタン時に新しいトランザクションを開始して最新状態を再検証する。確認時点のスナップショットは保証しない。

論理削除できる状態は `draft`、処理・確定待ちでない `active`、`paused`、`failed` とし、`completed`、`ended`、`deleted`、processing、claimed、sending、Schedule確定待ちは拒否する。再削除は成功扱いにせず、OperationLogを追加しない。削除時はScheduleを`deleted`、`next_run_at = NULL`、`deleted_at = terminal_at = updated_at = now`、`version + 1`とし、本文と定期設定および関連履歴を保持する。物理削除ワーカーの実装まではDiscord応答で30日後の自動削除を断定しない。

削除トランザクションは、ロックなしで対象Scheduleの内部参照、version、next_run_atを解決し、現在runとpending/processing runをID昇順でロックしてからScheduleをロックする。Scheduleロック後にrunをロックしてはならない。version、所有者、状態、next_run_at、run状態を再検証し、processingまたは確定待ちなら無変更で失敗する。pending runはDeliveryAttemptを作らず同じトランザクションで`skipped`、`next_attempt_at = NULL`、`finished_at = updated_at = now`、`result_code = 'schedule_deleted'`としてclaim・lease・message IDをNULLにする。終端runは変更しない。

Schedule、pending run、OperationLogは同一トランザクションで更新する。削除履歴は`action = 'deleted'`、`actor_type = 'user'`、実行者ID、検証済み理由、UTC削除日時を保存する。作成者本人は`creator_deleted`を優先し、管理者による他人の通常削除は`admin_deleted`、他人のfailed削除は`operator_resolved_failed`とする。`changes`には削除前後の状態とskipしたpending件数だけを保存し、本文、public_id、内部ID、versionを複製しない。RepositoryとApplication Serviceはcommitまたはrollbackしない。

### 6.3 `schedule_runs`：各回の実行履歴

| カラム | 型 | NULL | 説明 |
| --- | --- | --- | --- |
| `id` | BIGINT IDENTITY | 不可 | 内部主キー |
| `schedule_id` | BIGINT | 不可 | `schedules.id` への外部キー |
| `scheduled_for` | TIMESTAMPTZ | 不可 | 本来の実行予定日時 |
| `status` | VARCHAR(16) | 不可 | `pending`、`processing`、`succeeded`、`failed`、`skipped` |
| `attempt_count` | SMALLINT | 不可 | 完了した送信試行数。初期値0、最大4 |
| `next_attempt_at` | TIMESTAMPTZ | 可 | 初回または再試行を行える日時 |
| `claimed_by` | UUID | 可 | 処理権を取得したBotプロセスの起動ID |
| `claimed_at` | TIMESTAMPTZ | 可 | 処理権取得日時 |
| `lease_expires_at` | TIMESTAMPTZ | 可 | 送信開始前の処理権期限 |
| `discord_message_id` | BIGINT | 可 | 投稿成功時のDiscordメッセージID |
| `result_code` | VARCHAR(64) | 可 | 機械判定用の短い結果コード |
| `error_summary` | VARCHAR(500) | 可 | 秘密情報を除いたエラー概要 |
| `started_at` | TIMESTAMPTZ | 可 | この実行の処理開始日時 |
| `finished_at` | TIMESTAMPTZ | 可 | 成功、失敗、見送りの確定日時 |
| `created_at` | TIMESTAMPTZ | 不可 | 作成日時 |
| `updated_at` | TIMESTAMPTZ | 不可 | 更新日時 |

制約:

- 主キー: `id`
- 外部キー: `schedule_id -> schedules.id`、削除時は `RESTRICT`
- 一意制約: `(schedule_id, scheduled_for)`
- `attempt_count` は0～4
- 終端状態 `succeeded`、`failed`、`skipped` では `finished_at` 必須
- `succeeded` では `discord_message_id` 必須

主要インデックス:

- 部分インデックス `(next_attempt_at, scheduled_for)` WHERE `status = 'pending'`
- 部分インデックス `(lease_expires_at)` WHERE `status = 'processing'`
- `(schedule_id, scheduled_for DESC)`：予約詳細の履歴表示
- `(status, finished_at)`：障害確認と削除

### 6.4 `delivery_attempts`：各送信試行

送信開始前の異常終了と、送信済みか判断できない異常終了を区別するため、各試行を別テーブルへ保存する。

| カラム | 型 | NULL | 説明 |
| --- | --- | --- | --- |
| `id` | BIGINT IDENTITY | 不可 | 内部主キー |
| `schedule_run_id` | BIGINT | 不可 | `schedule_runs.id` への外部キー |
| `attempt_number` | SMALLINT | 不可 | 1～4 |
| `status` | VARCHAR(16) | 不可 | `claimed`、`sending`、`succeeded`、`failed`、`unknown` |
| `claimed_by` | UUID | 不可 | Botプロセスの起動ID |
| `claimed_at` | TIMESTAMPTZ | 不可 | 試行の処理権取得日時 |
| `send_started_at` | TIMESTAMPTZ | 可 | Discord送信直前の永続化日時 |
| `finished_at` | TIMESTAMPTZ | 可 | 試行終了日時 |
| `discord_message_id` | BIGINT | 可 | 成功時のメッセージID |
| `error_kind` | VARCHAR(32) | 可 | `transient`、`permanent`、`unknown` |
| `error_code` | VARCHAR(64) | 可 | 機械判定用コード |
| `error_summary` | VARCHAR(500) | 可 | 安全化した概要 |

制約とインデックス:

- 主キー: `id`
- 外部キー: `schedule_run_id -> schedule_runs.id`、削除時は `RESTRICT`
- 一意制約: `(schedule_run_id, attempt_number)`
- `attempt_number` は1～4
- `(status, claimed_at)`：異常終了試行の検索

### 6.5 `operation_logs`：操作履歴

| カラム | 型 | NULL | 説明 |
| --- | --- | --- | --- |
| `id` | BIGINT IDENTITY | 不可 | 内部主キー |
| `schedule_id` | BIGINT | 不可 | `schedules.id` への外部キー |
| `action` | VARCHAR(32) | 不可 | `created`、`edited`、`deleted`、`paused`、`resumed`、`completed`、`ended`、`failed` |
| `actor_type` | VARCHAR(16) | 不可 | `user`、`system` |
| `actor_user_id` | BIGINT | 可 | ユーザー操作時のDiscordユーザーID |
| `delete_kind` | VARCHAR(32) | 可 | `creator_deleted`、`admin_deleted`、`operator_resolved_failed` |
| `delete_reason` | VARCHAR(500) | 可 | 削除理由。削除操作では必須 |
| `changes` | JSONB | 可 | 変更項目。秘密情報や本文全体は保存しない |
| `created_at` | TIMESTAMPTZ | 不可 | 操作日時 |

制約とインデックス:

- 主キー: `id`
- 外部キー: `schedule_id -> schedules.id`、削除時は `RESTRICT`
- `actor_type = 'user'` では `actor_user_id` 必須
- `action = 'deleted'` では `delete_kind` と `delete_reason` 必須
- サーバー管理者が `failed` 予約を対処完了として削除する場合は `delete_kind = 'operator_resolved_failed'` とし、`delete_reason` を必須にする
- `(schedule_id, created_at DESC)`
- `(actor_user_id, created_at DESC)`

本文変更は `changes` に「本文が変更された」という事実だけを保存し、変更前後の本文は保存しない。

単発投稿の成功で予約を `completed` にした場合は `actor_type = 'system'`、`actor_user_id = NULL`、`action = 'completed'` の操作履歴を同じトランザクションで保存する。単発の最終失敗では `action = 'failed'`、本文あり定期投稿の終了では `action = 'ended'` を同様に保存する。

### 6.6 `notification_logs`：通知履歴

`notification_logs`は1つの論理通知における1つの送信経路を表すoutboxとする。フォールバックではrecipientを書き換えず別行を作り、各物理試行は`notification_attempts`へ保存する。draft通知はScheduleとScheduleRunの両方へ関連付け、投稿本文・通知本文は保存しない。

| カラム | 型 | NULL | 説明 |
| --- | --- | --- | --- |
| `id` | BIGINT IDENTITY | 不可 | 内部主キー |
| `schedule_id` | BIGINT | 可 | 関連予約。全体障害通知ではNULL可 |
| `schedule_run_id` | BIGINT | 可 | 関連実行。予約前通知ではNULL可 |
| `notification_type` | VARCHAR(32) | 不可 | `draft_24h`、`draft_1h`、`draft_immediate`、`run_failed`、`run_delayed`、`run_skipped`、`recovery` |
| `recipient_type` | VARCHAR(32) | 不可 | `creator_dm`、`operator_channel`、`operator_dm`、`log` |
| `recipient_id` | BIGINT | 可 | DiscordユーザーIDまたはチャンネルID。ログではNULL |
| `status` | VARCHAR(16) | 不可 | `pending`、`succeeded`、`failed` |
| `deduplication_key` | VARCHAR(160) | 不可 | 同じ通知の重複送信防止キー |
| `error_code` | VARCHAR(64) | 可 | 通知配送エラー専用コード。業務理由は保存しない |
| `error_summary` | VARCHAR(500) | 可 | 安全化した概要 |
| `created_at` | TIMESTAMPTZ | 不可 | 作成日時 |
| `sent_at` | TIMESTAMPTZ | 可 | 成功日時 |

制約とインデックス:

- 主キー: `id`
- 外部キー: `schedule_id -> schedules.id`、`schedule_run_id -> schedule_runs.id`
- 一意制約: `deduplication_key`
- `(status, created_at)`：未送信通知の検索
- `(schedule_id, created_at DESC)`：予約別通知履歴

本文や内部例外全文は通知履歴へ保存しない。

outboxには`scheduled_at`、`next_attempt_at`、`attempt_count`、claim・lease、開始・終了日時を持たせる。状態は`pending`、`processing`、`succeeded`、`failed`、`unknown`、`cancelled`とする。pending取得とprocessing lease復旧には部分インデックスを使用する。`notification_attempts`は通知経路ごとに最大3回の`claimed`、`sending`、`succeeded`、`failed`、`unknown`を記録し、送信成功時だけDiscord message IDを保存する。

## 7. 状態遷移

### 7.1 予約状態

| 現在 | 次 | 遷移を起こす処理 | 許可条件 |
| --- | --- | --- | --- |
| 新規 | `draft` | 予約作成 | 本文なし |
| 新規 | `active` | 予約作成 | 本文あり |
| `draft` | `active` | 予約編集 | 本文と未来の次回日時が有効 |
| `draft` | `deleted` | 予約削除 | 作成者または管理者 |
| `active` | `draft` | 予約編集 | 本文を未入力へ変更し、処理開始前 |
| `active` | `paused` | 一時停止 | 毎日・毎週だけ |
| `active` | `completed` | 単発投稿成功 | 単発だけ |
| `active` | `failed` | 単発の最終失敗 | 単発だけ |
| `active` | `ended` | 終了日当日の処理完了 | 毎日・毎週だけ |
| `active` | `deleted` | 予約削除 | 処理中ではなく、権限あり |
| `paused` | `active` | 再開 | 毎日・毎週、終了日前 |
| `paused` | `draft` | 再開または編集 | 毎日・毎週、本文なし |
| `paused` | `ended` | 終了判定 | 本文があり、終了日を過ぎて再開不能 |
| `paused` | `deleted` | 予約削除 | 権限あり |
| `failed` | `deleted` | `/post delete` | 作成者は自分の予約、サーバー管理者はすべての予約 |

採用判断:

- `paused` 予約の本文を編集しても、編集だけでは `active` に戻さない。再開操作が必要である。
- 一時停止中に本文を消した場合も状態は `paused` を維持し、再開時に本文の有無で `active` または `draft` へ遷移する。
- `draft` から `ended` へは遷移しない。終了日を過ぎた本文なしの `draft` は自動変更せず、利用者または管理者が確認して削除する。
- 終了日を過ぎた `paused` は、本文がある場合だけ `ended` へ遷移できる。本文がない場合は `paused` を維持し、本文を設定して終了処理するか、利用者または管理者が削除する。
- `failed` の再実行・編集機能はPhase 1では提供しない。運営者は予約一覧または詳細で失敗を確認し、必要な対応後に `/post delete` を実行する。新しい確認用コマンドは追加しない。
- サーバー管理者による `failed` 予約の削除を「運営者による確認・対処完了」とみなし、操作履歴を `operator_resolved_failed` として記録する。
- 上位要件の権限規則に従い、予約作成者も自分の `failed` 予約を削除できる。その場合は通常の作成者削除として `creator_deleted` を記録し、運営者による対処完了とは区別する。
- `completed`、`ended`、`deleted` は終端状態である。
- 単発投稿成功による `active` から `completed` への遷移では、システム操作履歴 `completed` を予約更新と同じトランザクションで保存する。

主な禁止遷移:

| 禁止遷移 | 理由 |
| --- | --- |
| 単発予約から `paused` | 単発の停止は削除で行うため |
| `completed` から任意の状態 | 投稿済み単発は変更しないため |
| `ended` から任意の状態 | 終了済み定期投稿は再開しないため |
| `deleted` から任意の状態 | 削除済み予約を復元しないため |
| `failed` から `active` | Phase 1では失敗予約を自動・手動再実行しないため |
| `paused` から `completed` | 一時停止は定期投稿だけに存在するため |
| `draft` から `ended` | `ended` は本文必須であり、本文なしの下書きを終了済みにしないため |
| 本文なしの `paused` から `ended` | `ended` は本文必須であり、DB制約に違反するため |

### 7.2 実行状態

| 現在 | 次 | 遷移を起こす処理 |
| --- | --- | --- |
| 新規 | `pending` | 単発作成、定期投稿の次回生成 |
| `pending` | `processing` | ポーラーが原子的に処理権を取得 |
| `pending` | `skipped` | 下書き、停止中、期限超過、終了後と判定 |
| `processing` | `pending` | 一時エラー後、次回再試行日時を設定 |
| `processing` | `succeeded` | Discord投稿成功 |
| `processing` | `failed` | 最大4回失敗、恒久エラー、送信結果不明 |
| `processing` | `skipped` | 送信前の再検証で投稿不可と確定 |

`succeeded`、`failed`、`skipped` は実行履歴の終端状態とし、他の状態へ戻さない。

主な禁止遷移:

- `pending` から直接 `succeeded`：処理権取得を省略できない。
- `succeeded`、`failed`、`skipped` から任意の状態：履歴を書き換えない。
- `processing` から `pending`：一時エラーで試行回数が4未満の場合だけ許可する。

## 8. 次回日時計算

### 8.1 共通ルール

- 計算は `zoneinfo.ZoneInfo("Asia/Tokyo")` を使用する。
- 利用者入力を日本時間のaware datetimeへ変換し、その後UTCへ変換して保存する。
- 次回日時は常に「基準日時より後」の最初の予定とする。
- 分単位で扱い、秒とマイクロ秒は0とする。
- `Asia/Tokyo` は夏時間を採用していないが、固定オフセット文字列ではなくIANAタイムゾーン名を使う。

### 8.2 単発

単発作成は `YYYY-MM-DD HH:MM`、`YYYY/M/D HH:MM`、`M/D HH:MM`、`今日 HH:MM`、`明日 HH:MM` の5形式だけをDomainパーサーで受理する。年省略は作成時刻から5分以上先となる次の実在日時を探索上限付きで求め、今日指定は境界未満でも翌日へ繰り越さない。入力された日本時間を既存の曖昧・不存在時刻検査を通してUTC awareへ変換し、確定後に`schedules.next_run_at` と最初の `schedule_runs.scheduled_for` に保存する。新規作成時は現在から5分以上先、ちょうど5分後を含む。

### 8.3 毎日

指定された `local_time` について、基準日時より後にある最初の日付を選ぶ。

```text
候補 = 基準日のlocal_time
候補 <= 基準日時なら候補日を1日進める
候補日 > end_dateなら次回なし
```

終了日当日の候補は実行対象に含める。

新規作成時だけは `now + 5分` を含む最初の候補を計算する。今日の指定時刻が境界以上なら今日、境界未満なら翌日とする。初回候補が終了日を超える場合は入力エラーとして何も保存しない。通常の実行確定時は従来どおり基準日時より厳密に未来の候補を求める。

### 8.4 毎週

指定曜日と `local_time` について、基準日時から0～6日先の候補を求める。同じ曜日でも時刻を過ぎていれば7日後とする。候補日が `end_date` と同じなら実行し、超えていれば次回なしとする。

新規作成時は `now + 5分` を含む最初の候補を計算する。当日が指定曜日で指定時刻が境界以上なら当日、境界未満なら7日後とし、別曜日なら次に到来する曜日とする。初回候補が終了日を超える場合は入力エラーとして保存しない。通常の実行確定時の厳密未来計算は変更しない。

### 8.5 編集と再開

- 編集後は、編集完了時刻を基準に次回日時を再計算する。
- 次回投稿の5分前を過ぎている場合は編集を拒否する。
- 未来の健全な通常初回runは停止中も保持し、予定時刻前の再開では同じrun IDを再利用する。Application Serviceはtransaction内の再検証結果を不変DTOの`held_run_reused`でBot層へ渡し、`true`の場合だけ成功Embedで保持していた投稿回を引き続き使用し、次回投稿日時が変更されていないことを明示する。
- 到来後は本人限定の非永続Viewで、次回から再開、同日分の即時処理、同日分の5分以上先への時刻指定、キャンセルを選ぶ。数日前の回と終了日超過後は救済しない。
- 次回が存在せず本文がある場合は `ended` へ遷移する。
- 次回が存在せず本文がない `paused` は `ended` へ遷移せず、本文を設定して終了処理するか削除する。本文なしの `draft` も自動的に `ended` へ変更せず、利用者または管理者が確認して削除する。
- 本文なしの定期 `draft` で現在回を `skipped` にした場合も、次回が存在すれば `draft` を維持して未来の次回実行を1件だけ生成する。次回が存在しなければ、DB制約上必要な既存の `next_run_at`、状態、versionを変更せず、確認・削除対象として残す。

一時停止と再開はそれぞれ `/post pause public_id:<canonical UUIDv7>`、`/post resume public_id:<canonical UUIDv7>` とし、理由、confirm、確認Viewなしで即時実行する。ローカル入力検証後にephemeralでdeferし、Botコマンドが所有するトランザクションをcommitした後に成功Embedをfollowupする。

pause／resume成功EmbedのPresenterは通常の予約情報を状態・種別から予約IDまで先に構成し、その後に処理結果と利用上の注意を原則1つの警告フィールドへまとめる。pauseは保持runの有無に応じて再開時の説明を切り替えつつ、全成功表示で停止中はDiscordへ投稿されないことを明示する。resumeは`held_run_reused`、resume mode、draft／endedの状態から正確な案内を組み立て、旧来の独立した再開結果フィールドを重複表示しない。

一時停止対象は処理・確定待ちでない`active`の毎日・毎週予約だけとする。runをID昇順でロック後にScheduleをロックする。未来、初回、attempt 0、`next_attempt_at = scheduled_for`、claim/leaseなし、DeliveryAttemptなしでScheduleと整合するrunを最大1件保持する。それ以外のpendingは`skipped`、`next_attempt_at = NULL`、`finished_at = updated_at = paused_at`、`result_code = 'schedule_paused'`とする。Scheduleは`paused`、`next_run_at = NULL`、`version + 1`とする。

再開対象は処理・確定待ちでない`paused`の毎日・毎週予約だけとする。保持runが未来なら同じrunを再利用する。到来済みならView確定時の新transactionで再検証し、`next_regular`では保持runをskipして次の通常runを作る。`immediate_once`と`rescheduled_once`では保持runをskipし、今回だけのpending runを作ってScheduleをそのrunへ向ける。本文ありはactive、本文なしはdraftとし、例外run完了後は基本recurrenceから次回を生成する。OperationLogは既存`resumed` actionに固定のresume modeと日時を保存する。

pause時は保持runに紐づく未claimのdraft通知だけを`cancelled(error_code='schedule_paused')`にする。対象はattempt 0、NotificationAttemptなし、claim/lease・started/finishedなしのpendingに限定する。draft再開時に同じ通知を再計画する場合も、このpause由来、attempt 0、Attemptなし、claim/leaseなしのcancelled行だけをpendingへ戻す。経過済み閾値は計画対象にせず、processing、succeeded、failed、unknownおよび他理由のcancelledは変更しない。

pause/resumeトランザクションは、ロックなしでguild付きpublic_idから内部参照、version、next_run_atを取得し、関係runをID昇順で`FOR UPDATE`してからScheduleを`FOR UPDATE`する。Scheduleロック後にrunをロックしてはならない。所有者、guild、version、状態、run状態を再検証し、processing、claimed、sending、Discord送信成功後のSchedule確定待ちは全体を無変更で拒否する。Schedule、run、OperationLogは同一トランザクションで更新し、RepositoryとApplication Serviceはcommitまたはrollbackせず、Discord API通信を行わない。

操作履歴は、pauseを`action = 'paused'`、resumeを`action = 'resumed'`、いずれも`actor_type = 'user'`、実行者ID、操作完了時刻で保存する。pauseの`changes`は状態の前後と見送ったpending件数、resumeは状態の前後と次回再計算の有無だけとし、本文、公開ID、内部ID、version、Discord message IDを保存しない。二重操作、認可失敗、競合失敗では履歴を追加しない。

## 9. 日時の扱い

- DBへ保存する日時はすべてUTCの `TIMESTAMP WITH TIME ZONE` とする。
- アプリケーション内ではタイムゾーン付き `datetime` だけを使用する。
- naive datetimeはドメイン境界で拒否する。
- Discordからの入力とDiscordへの表示は `Asia/Tokyo` とする。
- ログ日時はUTCのISO 8601形式とする。
- 終了日は時刻を持たない `DATE` とし、`Asia/Tokyo` の日付として解釈する。
- DBサーバーとBot実行環境はUTCを標準タイムゾーンに設定する。
- テストでは現在時刻を直接取得せず、差し替え可能なClockから受け取る。

## 10. DBポーリングと処理権取得

Bot起動後、`discord.ext.tasks.loop` を使って既定10秒間隔で実行対象を検索する。

処理対象:

- `schedule_runs.status = 'pending'`
- `next_attempt_at <= now()`
- 関連予約が `active`、または予定時刻を迎えた `draft`

1回の取得件数は既定20件とする。1回のループで取得した実行は、個別のasyncio Taskとして最大5件まで並行処理する。並行数はSemaphoreで制限する。

ポーラーは次の順に動作する。

1. 短いDBトランザクションを開始する。
2. 実行可能な行をロック付きで取得する。
3. `processing`、`claimed_by`、`claimed_at`、`lease_expires_at` を更新する。
4. `delivery_attempts` に `claimed` の行を作る。
5. コミットして行ロックを解放する。
6. 予約状態、権限、本文、投稿先を再検証する。
7. Discord送信直前状態をDBへ記録する。
8. Discord APIを呼ぶ。
9. 別の短いトランザクションで結果を保存する。

Discord APIへの通信中はDB行ロックを保持しない。

## 11. `SELECT FOR UPDATE SKIP LOCKED` による二重実行防止

処理権取得では、概念的に次のSQLを使用する。実装ではSQLAlchemyの `with_for_update(skip_locked=True)` を使用してよい。

```sql
SELECT id
FROM schedule_runs
WHERE status = 'pending'
  AND next_attempt_at <= now()
ORDER BY next_attempt_at, scheduled_for
FOR UPDATE SKIP LOCKED
LIMIT :batch_size;
```

同じトランザクション内で対象行を `processing` に更新し、対応する送信試行を一意制約付きで作成する。ほかのBotプロセスはロック済み行を待たずに読み飛ばす。

追加の防御:

- `(schedule_id, scheduled_for)` の一意制約で同じ予定回の重複生成を防ぐ。
- `(schedule_run_id, attempt_number)` の一意制約で同じ試行番号を重複作成しない。
- 更新時に現在状態と `claimed_by` をWHERE条件へ含める。
- Discord通信後は、処理権を取得した起動IDと一致する場合だけ結果を更新する。

## 12. Discord送信段階と異常終了

送信試行は次の段階を区別する。

| 試行状態 | 意味 | 異常終了時の扱い |
| --- | --- | --- |
| `claimed` | DB上の処理権を取得したが送信開始前 | リース期限後、安全に再取得可能 |
| `sending` | 送信直前の記録をコミット済み | 送信済みか判断不能として自動再送しない |
| `succeeded` | DiscordからメッセージIDを受領済み | 実行を成功確定 |
| `failed` | Discordから失敗を受領済み | エラー種別により再試行または最終失敗 |
| `unknown` | `sending` 中に停止し結果を確認できない | 自動再送せず運営者確認 |

送信直前に `delivery_attempts.status = 'sending'` と `send_started_at` を短いトランザクションで保存し、コミット後にDiscord APIを呼ぶ。このコミット前に停止した場合は「送信開始前」、コミット後から結果保存前に停止した場合は「送信結果不明」と判断する。

DBへの `sending` 記録と実際のHTTP送信は原子的にできないため、記録直後・API呼出前に停止した場合も安全側で `unknown` とする。この狭い範囲では投稿されない可能性があるが、二重投稿防止を優先する。

## 13. 最大4回の送信試行

1つの `schedule_runs` に対して、初回1回と再試行3回の最大4回を許可する。

| 試行番号 | 実行時刻 |
| --- | --- |
| 1 | 本来の予定時刻、または遅延投稿決定直後 |
| 2 | 1回目の一時エラーから1分後 |
| 3 | 2回目の一時エラーから5分後 |
| 4 | 3回目の一時エラーから15分後 |

Discordから待機時間を指定された場合は、その時刻を `next_attempt_at` に設定して上表より優先する。ただし試行回数は増える。

一時エラーでは、試行結果を `failed` として保存したうえで、実行履歴を `pending` に戻し、`next_attempt_at` を設定する。恒久エラー、4回目の失敗、送信結果不明では実行履歴を `failed` にする。

- 単発の最終失敗: 予約を `failed` にする。
- 定期投稿の1回の最終失敗: 予約は原則 `active` を維持し、次回分を生成する。

## 14. Bot停止・再起動時の復旧

Bot起動ごとにランダムな `worker_id`（UUID）を生成する。Discord接続完了後、通常ポーラー開始前に次を行う。

起動処理の開始時にClockからUTC awareな`recovery_cutoff`を一度だけ取得し、全バッチで固定する。期限切れprocessing Recoveryとpending Recoveryはこの同じ値を使用し、それぞれ独立して最大25バッチ（1バッチ1～20件）まで実行する。25バッチ目が満杯なら成功済みバッチはcommit済みのまま未完了として起動を停止し、通常ポーラーを開始しない。この処理ではBot runtimeがorchestration boundaryとしてバッチ単位のトランザクションを所有し、各Repositoryと業務Application Serviceはcommitまたはrollbackしない。

pending Recoveryは`FOR UPDATE SKIP LOCKED`で`scheduled_for, id`の安定順序によりrunを取得し、関係runを安定順序でロックしてからScheduleをロックする。Recovery中はDiscord APIを呼ばず、DB整理がすべて完了した後に通常ポーラーへ送信可能なpendingを渡す。processing Recoveryが失敗した場合はpending Recoveryを開始せず、pending Recoveryが失敗または未完了の場合もRecovery完了Eventを設定しない。Discord再接続では同一プロセスのRecoveryを再実行しない。

processing Recoveryは期限切れrunを`FOR UPDATE SKIP LOCKED`、対応する最新DeliveryAttempt、Scheduleの順でlockする。claimed attempt 1～3をretry予定の`pending`へ戻した場合はScheduleを確定しない。claimed attempt 4、sending/unknown、Attempt欠落または番号・worker・日時・状態不整合を安全側の`failed`へ終端化した場合だけ、同じSessionで既存`ScheduleExecutionService.finalize_run()`を呼ぶ。同一Sessionで既にlock済みのrunとScheduleを再lockしてもlock順はrunからScheduleのまま変わらない。単発activeは`failed`、`next_run_at = NULL`、version増加とsystem failed OperationLogを冪等に適用する。定期activeはrecurrence関数だけで固定cutoffより厳密に未来の未使用runを1件生成または正常な既存runを再利用し、次回がなければ`ended`とsystem OperationLogを適用する。paused/deleted/endedは復帰させず、不整合Scheduleは既存確定規則どおり拒否する。

run、Attempt、Schedule、新run、OperationLog、およびNotificationLogはorchestration boundary所有の同じトランザクションに含める。業務Application ServiceとRepositoryはcommit/rollbackしない。Schedule確定またはoutbox生成が失敗すれば当該バッチ全体をrollbackし、Recovery完了扱いにせず、後続Recoveryと投稿・通知・maintenanceの3 loopを開始しない。先にcommit済みのバッチは維持し、ログは固定イベント名と安全な件数だけに制限する。

業務イベント生成Serviceは`run_skipped`、`run_failed`、`run_delayed`、`recovery`を既存Notification typeへ変換し、最初の`operator_channel`経路だけをINSERTする。通常DeliveryはRun→Attempt→Schedule→NotificationLog、processing RecoveryはRun→Attempt→Schedule→NotificationLog、pending RecoveryはRun群→Schedule→NotificationLogの順を維持する。業務側は既存NotificationLogをlock/cancelせず、stale判定とfallback作成はLog→Attempt→Schedule→RunでlockするNotificationWorkerだけが行う。

run単位keyはSchedule canonical UUIDv7、Run予定UTC時刻、notification type、routeを含み、通常draft skipとstartup draft skipで共通とする。定期欠落回は`v1s|startup_recurring_missed|Schedule UUIDv7|recovery_cutoff UTC|run_skipped|route`の160文字以内のkeyで1 Schedule・1 cutoffへ集約し、`schedule_run_id = NULL`とする。内部ID、skip件数、本文、Discord message IDはkeyにも通知にも含めない。業務理由は`ScheduleRun.result_code`とnotification type、集約通知はtypeとrunなしの組合せからPresenterが固定文へ再構築し、NotificationLogの`error_code`と`error_summary`は生成時NULL、通知配送失敗時だけ使用する。投稿結果unknownを表すpending `run_failed` outboxと、通知配送自体のunknown終端を区別する。イベント生成者はoperator DMやlogを先行作成せず、fallbackはNotificationWorkerだけが作る。Recovery未完了そのものはDiscord通知対象外とする。

1. DB接続とマイグレーション状態を確認する。
2. 期限切れの処理権を持つ `processing` 実行を確認する。
3. `claimed` のままなら送信開始前として `pending` へ戻す。
4. `sending` のままなら試行を `unknown`、実行を `failed` とし、自動再送しない。
5. 期限を過ぎた単発を15分ルールで処理する。
6. 停止中に過ぎた定期投稿を `skipped` として記録する。
7. 定期投稿の未来の次回日時を計算する。
8. 終了日を過ぎた本文ありの定期投稿を `ended` にする。本文なしの `draft` または `paused` は自動変更せず、確認と削除が必要な予約として残す。
9. 復旧内容を操作・実行・通知履歴とログへ残す。
10. 運営者へ必要な通知を行う。
11. 通常ポーラーを開始する。

### 14.1 単発の期限超過

- 予定時刻から15分以内: 遅延投稿として実行し、運営者へ通知する。
- 15分超過: Discordへ送らず、実行履歴を `skipped`、予約を `failed` とし、運営者へ通知する。

遅延投稿の送信エラーには通常の最大4回ルールを適用する。

15分判定は初回の正常な`attempt_count = 0`のpendingだけに適用し、`recovery_cutoff - scheduled_for <= 15分`を期限内とする。15分ちょうどを含み、15分を1マイクロ秒でも超えれば期限超過とする。正常なattempt 1～3の再試行待ちpendingは予定時刻が古くても15分判定から除外し、`next_attempt_at`に従って通常ポーラーへ残す。単発draftは予定時刻到達時にrunを`draft_without_content`で`skipped`とするが、Scheduleの`draft`、`next_run_at`、versionを維持する。

### 14.2 定期投稿の期限超過

- 過去の各回は送信せず `skipped` として記録する。
- 停止期間とスキップ件数を記録し、運営者へ通知する。
- 復旧時刻より後の最初の予定だけを `pending` で作成する。
- 終了日当日の予定は対象に含める。
- 次回が存在せず本文がある場合は予約を `ended` にする。
- 次回が存在しない本文なしの `draft` または `paused` は `ended` にせず、利用者または管理者が本文を設定して終了処理するか削除する。
- 本文なしの定期 `draft` の `skipped` 回を確定するときは、復旧時刻より後の次回があれば `draft` のまま1件だけ生成する。次回がなければ既存の `next_run_at` とversionを維持し、確認・削除対象として残す。

長期間停止して大量の履歴が必要になった場合も、Phase 1ではスキップした各予定回を個別の `schedule_runs` として保存する。

各定期予約で1回の起動時に補完する過去発生回は最大500回とし、超えた場合はRecovery未完了として通常ポーラーを開始しない。既存の終端runを再利用せず、未来日時も履歴で使用済みなら次の未使用発生日時へ進む。`paused`および既に終端状態のScheduleにpendingが残る場合はrunだけを安全に終端化し、Schedule状態を自動修復しない。startupでは終了日を過ぎた`paused`を`ended`へ変更しない。

pendingとDeliveryAttemptの状態が一致せず二重送信を否定できない場合、runを`startup_inconsistent_pending`で`failed`にして既存Attempt履歴は変更しない。単発activeだけはScheduleも`failed`にし、定期および既に終端状態のScheduleは現在状態を維持する。この業務処理は同じtransactionでRecovery用NotificationLogを冪等生成する。

## 15. 下書き通知と運営者通知

### 15.1 下書き通知

下書き通知もDBポーリングで判定する。毎回の予定日時に対して次を実施する。

- 24時間前に本文がなければ作成者へDMする。
- 1時間前にも本文がなければ作成者へDMする。
- 24時間前を過ぎて作成された場合は24時間前通知を作らない。
- 1時間前も過ぎて作成された場合は `draft_immediate` を作成直後に送る。
- 一時停止中の定期投稿には送らない。
- `deduplication_key` の一意制約で同じ通知を二重送信しない。

下書きDMに失敗した場合は運営者通知へ切り替える。

24時間・1時間境界は包含する。停止中に過ぎた事前通知は後追いせず、復旧時点で予定前かつ残り1時間未満のdraftだけ`draft_immediate`を発生回ごとに1回生成する。activeからdraftへの編集も残り時間に応じて24時間・1時間・即時を同じ規則で選ぶ。pause、delete、active化、日時編集で不要になった未送信通知は送信前再検証で`cancelled`とする。

### 15.2 運営者通知

共通の通知サービスが次の順で処理する。

1. 環境変数で指定した運営者通知チャンネルへ投稿する。
2. 失敗したら、環境変数で指定したBot運営者へDMする。
3. 両方失敗したら `ERROR` ログへ記録する。

通知には予約ID、実行履歴ID、発生時刻、投稿先、短い原因、必要な対応を含める。Botトークン、DB接続情報、投稿本文、内部例外全文は含めない。各経路の成功・失敗を `notification_logs` に残す。

通知はDM、operator channel、operator DMで共通のEmbedを1個使用する。固定タイトル・説明、日本語状態、BotがDBから取得・検証したchannel IDによる`<#channel_id>`形式、JST日時、完全な予約UUIDv7、必要な対応を表示する。投稿本文と本文プレビューは含めず、`AllowedMentions.none()`相当ですべての通知を無効にする。Embedはtitle 256文字、description 4,096文字、Field名256文字、Field値1,024文字、Field数25、合計6,000文字をApplication層で送信前に上限検証する。`recipient_type=log`はDiscordへEmbedを送信せず、安全な固定ERRORイベントだけを記録する。transientだけを1分後・5分後に再試行し、初回を含め最大3回とする。Rate Limitは未来のRetry-Afterを優先し、permanentまたは上限到達ではフォールバックを許可する。unknownは再送もフォールバックもしない。通知Workerは起動Recovery完全成功後だけ開始し、Recovery未完了、DB障害、通知Worker自身の障害は安全なERRORログと外部監視へ委ねる。バックグラウンド通知はephemeralではなく、追加IntentやAdministrator権限を要求しない。

NotificationWorkerは予約投稿Workerと独立した`tasks.loop`で逐次サイクルを実行する。Transaction Aでdue行を`FOR UPDATE SKIP LOCKED`によりclaimしてcommitし、Transaction BでLog、Attempt、関連Schedule/Runを再検証してsendingまたはcancelledへ更新してcommitする。DB Sessionを閉じた後にGatewayを1回だけ呼び、Transaction Cでsuccess、retry、failed、unknownと必要なfallbackを保存する。Gateway成功後にTransaction Cが失敗した場合はsendingを維持し、再送せずlease Recoveryでunknownへ移す。

送信前再検証で不要になった通知はNotificationLogを`cancelled`とし、Attemptにはcancelled状態がないため、Discord送信を行わなかった確定結果として`failed / permanent / notification_stale`へ終端化する。このAttemptは配送障害の再試行やfallback対象にはしない。

通知Gatewayはconfigured guildのキャッシュ済みTextChannelと権限を検証し、DMは`get_user`、cache miss時だけ`fetch_user`を使用する。Bot自身、固定operator ID不一致、別guild、TextChannel以外を拒否し、`fetch_channel`、独自sleep、独自retryを使用しない。fallbackは`creator_dm → operator_channel → operator_dm → log`で別NotificationLogとして作り、元の論理イベントkeyを維持して経路だけを変える。

下書き事前通知はScheduleRun生成・置換と同じcaller-owned transactionで`creator_dm` outboxをINSERTする。`remaining > 24h`と`=24h`は`draft_24h`と`draft_1h`、`1h < remaining < 24h`と`=1h`は`draft_1h`、`0 < remaining < 1h`は`draft_immediate`、`remaining <= 0`は生成なしとする。keyはSchedule UUIDv7、Run時刻、type、routeから作り、旧Run行を業務Serviceからlock/cancelしない。

起動時は予約processing、期限超過pending、notification lease、draft notification bootstrapの順に同じcutoffで各最大25 batchを処理し、すべて完了した後だけRecovery Eventを設定して投稿・通知・maintenanceの3 loopを開始する。bootstrapはfuture draftのRunを`scheduled_for, id`順に`FOR UPDATE SKIP LOCKED`で取得してからScheduleをlockし、cutoffより前の未claim pending通知のみcancelする。過ぎた24h/1hは再生成せず、残り1時間未満ならRun単位でimmediateを最大1件作る。停止時は3 loopをstop/cancelしてTaskを回収してからstartup Task、確認View、Discord Client、Engineを閉じる。

下書き事前通知、Processing Recovery後のSchedule確定、業務イベントからのNotificationLog生成、NotificationWorkerによるDiscord送信とfallbackはruntimeへ接続する。30日物理削除は独立したmaintenance loopが行う。実Discordでの確認状況は[手動受入チェックリスト](manual-acceptance-phase1.md)で管理する。

## 16. 30日後の自動削除

Schedule関連通知は親Scheduleの物理削除前に明示削除する。global通知は`Schedule ID IS NULL AND ScheduleRun ID IS NULL`と定義し、終端状態の`finished_at`から30日後に削除する。Schedule IDだけNULLでRunに関連する通知はglobal扱いしない。`failed`状態のScheduleと関連通知は自動削除せず、権限を持つ作成者または管理者が論理削除した後はdelete kindや削除前状態で区別せず、`deleted`の`terminal_at`から30日保持する。

Bot内の保守ループを1日1回、日本時間04:00に実行する。APSchedulerは使用せず、`discord.ext.tasks.loop(time=...)` を利用する。

1サイクル開始時にClockからUTC awareな`cleanup_cutoff`を1回だけ取得し、全transactionで`retention_cutoff = cleanup_cutoff - timedelta(days=30)`を固定する。naiveまたは非UTCを拒否し、30日ちょうどを削除対象に含める。

Schedule削除対象:

- `completed`、`ended`、`deleted` の予約
- `terminal_at IS NOT NULL AND terminal_at <= retention_cutoff`
- 関連する `schedule_runs`
- 関連する `delivery_attempts`
- 関連する `operation_logs`
- 関連する `notification_logs`

保持期間を満たしても、pending/processing ScheduleRun、claimed/sending DeliveryAttempt、pending/processing NotificationLog、claimed/sending NotificationAttemptが1件でもあれば削除しない。unknownは終端状態としてin-flight除外に含めない。候補を`terminal_at, id`順の`FOR UPDATE SKIP LOCKED`で1件ずつ取得し、短い`SET LOCAL lock_timeout = '1s'`をcleanup transaction内だけに設定して全条件を再検証する。

全FKはRESTRICTであるため、1 Scheduleの同じtransactionで、(1) Scheduleに関連するNotificationLogのNotificationAttempt、(2) Scheduleへ直接または配下Run経由で関連するNotificationLog、(3) DeliveryAttempt、(4) OperationLog、(5) ScheduleRun、(6) Scheduleの順に明示削除する。1 Scheduleにつき1 Session・1 transactionとし、1サイクル最大100件まで処理する。global通知は別枠最大100件とし、1 NotificationLogごとにNotificationAttempt、NotificationLogの順で1 transactionにより削除する。101件目は翌日以降へ残し、成功済みtransactionは後続の失敗で戻さない。

`failed` 状態の予約は30日自動削除の対象外とする。権限を持つ作成者または管理者が`/post delete`を実行して`deleted`になった時点から30日を数える。

上位要件の権限規則に従い、`failed`予約を削除できるのは、その予約の作成者とサーバー管理者である。作成者による削除は`creator_deleted`とし、運営者による対処完了とは扱わないが、作成者による明示的削除として30日保持の開始条件になる。サーバー管理者が`failed`予約を削除した場合は`operator_resolved_failed`、それ以外は`admin_deleted`として操作履歴へ保存する。deleted後の物理削除可否は削除前状態やdelete kindで分けない。

独立した保守loopを`discord.ext.tasks.loop(time=Asia/Tokyo 04:00)`で1日1回実行し、Bot起動直後には実行しない。全startup RecoveryとBootstrap成功後だけ開始し、再接続時の二重startと1サイクルの重複を防ぐ。lock timeout、deadlock、FK競合、内部エラーは当該対象だけrollbackして翌日再試行し、cleanupの失敗または未完了で予約投稿loopや通知loopを停止しない。RepositoryとApplication Serviceは対象行の状態を修復せず、cleanupからDiscord APIやNotificationLog自己通知を呼ばない。

ログには固定イベント名、固定cutoff、各テーブルの安全な削除件数、残件数、内部エラー件数、未完了フラグだけを残す。public ID、内部ID、本文、Discord ID、秘密情報、SQLパラメーター、例外全文、tracebackを含めない。OperationLogを含む個別履歴はScheduleと同時に削除し、物理削除専用OperationLogや別監査テーブルは作らない。すでに投稿済みのDiscordメッセージは削除しない。

## 17. Discord権限と `allowed_mentions`

### 17.1 利用者権限

- 対象サーバーIDが設定値と一致することを最初に確認する。
- 予約作成はサーバー管理者または許可ロール所有者だけに許可する。
- 作成者は自分の予約だけを操作できる。
- サーバー管理者は対象サーバーのすべての予約を操作できる。
- 権限はコマンド受付時とDB更新直前に確認する。
- ロール名ではなくロールIDを使用する。

### 17.2 Discord Intents

Phase 1ではPrivileged Members Intentを原則使用しない。スラッシュコマンドの `Interaction` に含まれるメンバー、権限、ロール情報を使って判定し、全メンバー一覧の取得やメンバーキャッシュへ依存しない。

実装検証で必要な権限・ロール情報を取得できないことが判明した場合だけ、取得できない具体的情報、代替案、追加権限の影響を文書化して再検討する。理由を記録せず有効化してはならない。

`message_content` Intentも使用しない。BotへAdministrator権限を付与しない。

### 17.3 メンション

- 登録・編集時に `@everyone` と `@here` を含む本文を拒否する。
- UnicodeやDiscord表記の差を考慮し、送信時制限も必ず行う。
- Phase 1ではユーザー、ロール、返信のメンション解析も無効にする。
- Discord送信時は毎回、概念的に `AllowedMentions(everyone=False, users=False, roles=False, replied_user=False)` を明示する。
- ライブラリのグローバル既定値だけに依存しない。

## 18. 環境変数

| 環境変数 | 必須 | 例 | 用途 |
| --- | --- | --- | --- |
| `APP_ENV` | 必須 | `development` | `development`、`test`、`production` |
| `LOG_LEVEL` | 任意 | `INFO` | ログレベル。既定 `INFO` |
| `TIMEZONE` | 必須 | `Asia/Tokyo` | Phase 1ではこの値以外を拒否 |
| `DISCORD_BOT_TOKEN` | 必須 | 空欄 | Botトークン |
| `DISCORD_GUILD_ID` | 必須 | `123456789012345678` | 対象サーバーID |
| `DISCORD_ALLOWED_ROLE_IDS` | 必須 | `111...,222...` | カンマ区切りの許可ロールID。最低1件 |
| `DISCORD_OPERATOR_USER_ID` | 必須 | `123...` | 通知フォールバック先の運営者 |
| `DISCORD_OPERATOR_CHANNEL_ID` | 必須 | `456...` | 運営者通知チャンネル |
| `DATABASE_URL` | 必須 | `postgresql+psycopg://...` | PostgreSQL接続URL |
| `SCHEDULER_POLL_INTERVAL_SECONDS` | 任意 | `10` | ポーリング間隔。既定10 |
| `SCHEDULER_BATCH_SIZE` | 任意 | `20` | 1回の取得件数。既定20 |
| `SCHEDULER_MAX_CONCURRENCY` | 任意 | `5` | Discord送信の最大並行数。既定5 |
| `SCHEDULER_PROCESSING_TIMEOUT_SECONDS` | 任意 | `120` | 送信開始前リースの期限。既定120 |

確定要件である再試行間隔、最大4回、15分の復旧猶予、30日の保存期間は、Phase 1では環境変数にせず型付き定数とする。

設定は起動時に一度読み込み、不足、不正な数値、重複ロール、対象外タイムゾーンを検出したらDiscordへ接続せず終了する。秘密値を設定オブジェクトの文字列表現やログへ出さない。

## 19. ログ設計

Python標準 `logging` を使用する。開発環境は人が読みやすい形式、本番環境は1行JSON形式とする。

共通項目:

- UTCのタイムスタンプ
- ログレベル
- イベント名
- `worker_id`
- 予約ID
- 実行履歴ID
- 送信試行IDと試行番号
- DiscordサーバーID、チャンネルID、利用者ID
- エラー分類と安全化した概要

主なイベント:

- Bot起動・停止
- DB接続・マイグレーション不一致
- 処理権取得
- 投稿成功・一時失敗・最終失敗・結果不明
- 再試行予約
- 復旧開始・完了
- 下書き通知・運営者通知
- 通知フォールバック失敗
- 自動削除結果

投稿本文、Botトークン、DBパスワード、完全な接続URL、内部例外全文を通常ログへ出さない。開発時の例外スタックも、秘密値をマスクしたうえで `ERROR` ログへ出す。

## 20. テスト設計

### 20.1 単体テスト

外部通信と実DBを使わず、次を検証する。

- 単発、毎日、毎週の次回日時
- `Asia/Tokyo` とUTCの変換
- 終了日当日を含む判定と `ended`
- 予約・実行の許可遷移と禁止遷移
- 単発の一時停止・再開拒否
- 5分前の編集制限
- 15分以内・超過の復旧判定
- 1分、5分、15分と最大4回
- 下書き通知時刻
- `@everyone`、`@here` の拒否
- 権限判定
- 30日後の削除判定

現在時刻は差し替え可能なClockを使用し、実際の待機を行わない。

### 20.2 PostgreSQL統合テスト

SQLiteでは代用せず、テスト専用PostgreSQLを使用する。

- Alembicを空DBへ適用できる
- ORMモデル、NULL、CHECK、一意制約、外部キー
- Repositoryの作成・更新・一覧
- 同時処理で1プロセスだけが処理権を取得する
- `FOR UPDATE SKIP LOCKED`
- 試行番号と予定回の重複防止
- 状態更新の競合検出
- 起動時復旧
- 30日後の関連データ削除

テスト用DB名に明示的な接尾辞を要求し、本番URLに見える接続先では破壊的テストを拒否する。

### 20.3 Discord境界テスト

Discord APIを実際に呼ばず、Gatewayを偽物へ差し替える。

- 投稿成功とメッセージID保存
- 一時エラー、恒久エラー、Rate Limit
- 送信開始前停止と送信結果不明
- DM失敗、チャンネル通知失敗、両方失敗
- `allowed_mentions` の明示設定
- 対象外サーバー、一般メンバー、他人の予約の拒否
- 本人だけに見える応答

### 20.4 手動確認

対象Discordサーバーと開発DBを使用し、要件書の完成条件をチェックリストとして確認する。Bot停止・再起動、チャンネル削除、権限剥奪、DM拒否、定期投稿終了も含める。

## 21. Docker Compose構成

ComposeではPostgreSQLサービスだけを起動する。

```text
services:
  postgres:
    image: postgres:<固定したメジャー・マイナーバージョン>
    ports:
      - 127.0.0.1:<開発用ポート>:5432
    environment:
      POSTGRES_DB: <開発DB>
      POSTGRES_USER: <開発ユーザー>
      POSTGRES_PASSWORD: <開発専用パスワード>
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck: ...
```

採用方針:

- PostgreSQLイメージは実装開始時の安定版から具体的なバージョンへ固定する。
- ポートは `127.0.0.1` だけへ公開し、外部ネットワークへ公開しない。
- 名前付きVolumeで再起動後もデータを保持する。
- ヘルスチェックには `pg_isready` を使う。
- Composeファイルへ本番資格情報を書かない。
- Bot、Alembic、pytest、RuffはWSLの `.venv` で動かす。
- 開発DBとテストDBを分離する。

## 22. 実装順序

1. `pyproject.toml` をCPython 3.14へ統一し、必要な依存を追加する。
2. `.env.example` と設定読込・起動時検証を整備する。
3. PostgreSQLのComposeと接続確認手順を作る。
4. SQLAlchemyのDB基盤とAlembicを導入する。
5. 本書のテーブルと初回マイグレーションを作る。
6. 日時計算、状態遷移、本文検証をドメイン層へ実装する。
7. Repositoryと予約作成・一覧・編集・削除を実装する。
8. 処理権取得と送信試行記録を実装する。
9. 偽物のDiscord Gatewayで単発投稿と最大4回の再試行を完成させる。
10. 毎日・毎週、pause、resume、endedを実装する。
11. 起動時復旧と送信結果不明の処理を実装する。
12. 下書き通知、運営者通知、フォールバックを実装する。
13. 30日後の自動削除を実装する。
14. discord.pyのBotとスラッシュコマンドを接続する。
15. 権限、Intents、`allowed_mentions` を対象サーバーで確認する。
16. PostgreSQL統合テストと手動テストを完了する。
17. セットアップ、マイグレーション、起動、停止、バックアップ、復元、障害対応を文書化する。

各段階で単体テストを追加し、後の段階までまとめてテストを先送りしない。

## 23. Phase 2への拡張方針

### 23.0 予約ID Autocomplete

`ScheduleQueryService.autocomplete_schedules`は操作種別、検証済み入力、Bot層が解決したchannel ID集合を受け、短時間のSessionで最大25件の不変DTOを返す。Repositoryはguild、creator、Schedule状態を必須条件とし、UUID、種別、状態、数値channel ID、channel ID集合をOR条件で重複なく統合する。操作系では相関subqueryによりprocessing run、claimed/sending attempt、current run不整合を保守的に除外する。`SELECT FOR UPDATE`、全ScheduleのPython読込、flush、commit、明示rollbackは使用しない。

Bot層では5コマンドに薄いcallbackを登録し、共通処理へ操作種別を渡す。`Phase1CommandTree.interaction_check`はautocomplete認可失敗時だけ通常メッセージを送らず、空のautocomplete結果を一度返す。callbackは既存`is_authorized_interaction`を再利用して再確認し、DB・表示例外を固定イベント名へ変換して空候補を返す。

候補DTOはpublic UUID、channel ID、creator ID、種別、状態、表示日時だけを持つ。Bot層は前後空白と先頭`#`1個を除去してcasefoldし、設定guildのキャッシュ済みTextChannel一覧から完全・前方・部分一致するIDを解決する。実行者の`view_channel`権限を確認し、別guildと非TextChannelを除外する。NFKC、曖昧検索、REST `fetch_channel`は使わず、危険文字や長すぎる入力は空候補とする。Choice生成はSession終了後に`guild.get_channel`だけで表示名を再解決し、本文、内部DB ID、Discord message ID、worker ID、例外情報はApplication DTOとChoiceへ渡さない。

### 23.1 予約詳細View基盤

`ScheduleQueryService.get_schedule_detail`はSchedule表示値と正のversion、操作可否観測を1つのread-only SELECTで取得し、Session終了後も利用できる不変`ScheduleDetail`へ変換する。相関subqueryでcurrent run件数・pending件数・pristine違反・processing・claimed/sending/unknown attemptを数え、状態・種別・`now + 5分`と組み合わせて4操作の表示用可否を保守的に算出する。SELECT FOR UPDATE、flush、commit、明示rollback、ORMのBot層返却は行わない。

`ScheduleDetailView`はcanonical UUIDv7、観測version、操作者ID、可否DTOと、一覧由来の場合だけstatus・schedule_type・pageを保持する。Session、transaction、内部DB ID、本文、guild IDは保持せず、custom IDは固定値だけを使う。第2段階では可否DTOに応じた一時停止・再開・削除と、一覧由来の「一覧へ戻る」を描画する。

一覧から詳細へ移るときは同じephemeralメッセージを編集し、旧`ScheduleListView`をstopしてregistryから除去してから新しい詳細Viewを登録する。戻るときは予約所有境界と最新一覧を短いread Sessionで再確認し、保存したfilter・type・pageを使ってclamp付きで新しい一覧Viewへ移管する。両Viewを同時にregistryへ残さない。

一覧・詳細Viewは`timeout=None`、`asyncio.Lock`、finished/closedを持つ。discord.py 2.7.1がephemeral初回送信時の`None`を900秒へ変換するため、初回登録中だけ公開timeout値`0.0`を使用してtimeout Task生成を抑止し、登録直後に`None`へ戻す。ViewStoreの内部書換えやMonkey Patchは行わない。同一メッセージ更新前に旧Viewをstopし、固定custom_idのdispatch所有権を新View一つへ移す。Bot closeは一覧・詳細・作成・削除・再開Viewと開いているModalをstopし、各waitを`gather(return_exceptions=True)`で回収する。persistent Viewとして起動時登録せず、再起動復元も行わない。削除されたephemeralメッセージはtimeout応答を試みず、closeまたは遷移でregistryから回収する。

検索は固定語彙、17～20桁のchannel ID、canonical UUID形式の前方一致に限定する。完全UUIDはUUIDv7を検証する。日時、channel名、本文、曖昧・自然言語検索は行わない。

### 23.2 予約詳細の状態操作

- `Detail → ResumeChoice → Detail`と`Detail → DeleteConfirm → Detail`では、遷移元をstopしてregistry所有権を一つだけ移譲する。
- pause／resume／deleteはoptional `expected_version`を受け、slash commandは未指定、詳細操作は表示時versionを指定する。
- 更新Serviceは既存のrun ID順lock、attempt確認、Schedule lock、snapshot再検証を維持し、commit／rollbackを所有しない。
- Discord表示はcommitとSession終了後に最新`ScheduleDetail`を取得して更新し、表示失敗をDB rollbackへ結び付けない。
- 第3段階の編集は`Label`配下の`ChannelSelect`、`StringSelect`、`TextInput`で種別別Modalを構成する。単発3、毎日4、毎週5トップレベル部品とし、1回のsubmitを1回の`ScheduleEditingService.edit`と1 transactionへ変換する。
- 空本文は`clear_content`、空終了日は`clear_end_date`へ排他的に変換する。詳細表示時versionを`expected_version`としてsnapshot取得時とSchedule lock後に検証し、no-opと競合はいずれもOperationLogを作らず最新詳細へ戻す。

### 23.3 AI文章作成

AIプロバイダーを `infrastructure/ai/` に置き、アプリケーション層の文章生成ユースケースからインターフェース越しに呼ぶ。Discordコマンドや予約RepositoryからAI SDKを直接呼ばない。生成結果は利用者が確認してから予約本文へ反映する。

### 23.4 文体反映

文体設定と本人が登録した例文を予約本文から分離したテーブルへ保存する。AIへ送る情報を組み立てる処理はアプリケーション層へ置き、削除・保存期間・同意を管理できるようにする。

### 23.5 PAY.JPサブスクリプション

PAY.JP顧客ID、契約ID、契約状態を専用テーブルへ保存する。カード情報は保存しない。Webhook受信用Webプロセスを追加し、Discord Botと同じアプリケーションサービス・Repositoryを再利用する。Webhookの重複受信に備えてイベントIDを一意に保存する。

### 23.6 複数サーバー対応

Phase 1から全予約に `guild_id` を持たせる。Phase 2では環境変数の単一サーバー設定を `guild_settings` テーブルへ移し、許可ロール、運営者チャンネル、タイムゾーン、契約をサーバー単位で管理する。すべてのRepository検索に `guild_id` 条件を含め、サーバー間のデータ参照を防ぐ。

### 23.7 プロセス分離

負荷が増えた場合、Bot、予約ワーカー、Webhook APIを別プロセスへ分離する。PostgreSQLの処理権取得をすでに採用しているため、Phase 1の予約データと排他方式を維持したままワーカー数を増やせる。Celeryなどの追加は、DBポーリングで運用上の限界が確認された場合に改めて判断する。

## 24. Phase 3とPhase 2後の将来設計

本章はPhase 2の完了条件に含めないPhase 3の設計である。24.1だけはPhase 3第1段階として実装済みであり、24.2以降は未実装の将来設計である。後続段階の実装時に要件、詳細設計、Migration、外部AIのプライバシー条件、受入項目を改めて確定する。第1段階ではDBモデルとAlembic Revisionを変更しない。

### 24.1 `/post show`の削除済み候補

`ScheduleRepository.autocomplete_schedules()`の`show`用許可状態だけから`deleted`を除外する。操作別許可状態と検索条件はANDで結合されるため、空入力、状態、種別、UUID、channel ID、Discordキャッシュから解決したchannel IDのOR検索のすべてで`deleted`を返さない。最大25件、`next_run_at ASC NULLS LAST, id ASC`、読み取り専用の射影を維持する。

論理削除されたScheduleと監査履歴はDBへ保持し、`/post list status:削除済み`の一覧および一覧由来の詳細取得対象にする。canonical UUIDの直接入力による`/post show`は`deleted`詳細を取得可能なままにする。delete、edit、pause、resumeのquery、`_STATUS_SEARCHES`、Presenter、Application Serviceの既存状態境界は変更しない。DB更新、row lock、Discord RESTによるchannel取得は追加せず、失敗時は空候補と固定ログを維持する。

### 24.2 AI予約名

AI予約名の生成はDB transaction外の任意アプリケーションユースケースとして設計する。用途は現在の投稿本文から最大32文字の名前を1件生成することだけに限定し、改行・制御文字を拒否する。AI呼び出し中にSession、transaction、row lockを保持せず、生成失敗を予約作成、編集、投稿の成否から切り離す。

将来の永続化候補は`schedules.display_name VARCHAR(32)`と`schedules.display_name_source`とし、sourceは`ai`、`manual`、`unset`の閉じた値とする。既存行は`display_name = NULL`、`source = unset`へ移行する。`unset`は保存名なし、`ai`と`manual`は安全化済みの1～32文字を必須とするCHECK制約を設計する。AI入力・応答履歴用テーブルは作成しない。

`manual`は本文変更後も維持する。`ai`の状態で本文が変更された場合は、本文更新transactionで古い名前を解除して`unset`とし、commit後に新しい本文と観測Schedule versionだけで再生成を1回試す。別の短いtransactionでversionと状態を再検証し、一致時だけ`ai`として保存する。失敗または競合時は古いAI名を復元しない。AI生成名は親Scheduleと同じ保持境界で物理削除する。

非AIフォールバックはDBへ保存せず、Scheduleの種別と日時から`単発予約 M/D HH:MM`、`毎日予約 HH:MM`、`毎週予約 曜日 HH:MM`を決定的に構築する。必要な日時がない場合は`名称未設定`とする。一覧、詳細、Autocomplete、確認画面は保存済み名称またはフォールバックを表示するだけでAIを呼ばず、本文の一部を名称として使わない。

AI providerは`infrastructure/ai/`のインターフェース背後へ置き、初期状態では無効にする。利用者ごとの学習、プロファイル、過去投稿検索、Embedding、ベクトルDB、ファインチューニング、学習データセットは実装しない。providerへ渡すのは現在本文と固定生成条件だけとし、Discord ID、利用者ID、channel ID、予約ID、内部ID、過去投稿、履歴を渡さない。APIキー、本文、AI入力全文、AI応答全文を通常ログへ記録しない。

### 24.3 AI費用制御

同じSchedule versionにつき最大1回、timeout 5秒、自動再試行なしとする。初期上限は1日50回、1か月500回、月額100円相当とし、回数または費用のどちらかが上限に達した場合、費用集計が不明または失敗した場合はproviderを呼ばない。低価格モデル、短い固定入力、最大32文字の出力を前提とし、provider決定後はprovider側の請求警告と上限も設定する。

費用制御はApplication層のprovider呼び出し前に適用する。永続的な上限管理が必要な場合は、本文、生成名、Discord ID、利用者ID、予約IDを持たない日次・月次集計をMigration候補とする。providerと課金単位が未決定のため、集計テーブルの列と費用換算方法は実装開始時に確定する。

一覧、詳細、Autocomplete、投稿Worker、Recovery、通知WorkerはAIユースケースへ依存しない。AI無効、上限到達、timeout、異常応答、費用集計失敗でも既存の予約処理を継続する。

### 24.4 データ保持と配置

現行の`completed`、`ended`、`deleted`と関連履歴の30日cleanupを維持する。`failed`は最終更新から90日、`draft`と`paused`は最終更新から180日を将来の整理候補とするが、状態制約、削除・匿名化方法、30日前通知、通知失敗時の扱いを確定するまで有効化しない。特に`draft`と`paused`を無通知で自動削除しない。

アプリケーションログは14日、DBバックアップは日次7世代かつ最大14日を運用上限とする。期限超過ログとバックアップの削除を配置環境側で自動化し、復元後はDB cleanupを適用する。バックアップから即時消去できない期間を利用者向け方針へ明記する。Discordへ投稿済みメッセージはDB cleanup対象外とする。

現在はローカル開発を継続し、一般公開準備まで常時稼働環境を構築しない。開発、公開前限定テスト、本番でDiscord Application、DB、秘密情報を分離する。公開前限定テスト合格後に無料環境を第一候補として配置し、不足時だけ有料環境を検討する。DB、Bot、AI providerの境界を保ち、クラウド固有機能を必須にせずDockerで移行可能にする。詳細な順序と公開ゲートは[開発・公開ロードマップ](development-roadmap.md)を正本とする。
