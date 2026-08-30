# Discord AI Reminder Bot

Discordへテキストを予約投稿するBotです。Phase 1とPhase 2は受入完了済みで、環境変数で指定した1つのguildを対象に、予約の作成・管理・実行・復旧・通知・保持期限後の削除、予約ID Autocomplete、予約詳細Viewを実装しています。

## 現在利用できる機能

- 単発、毎日、毎週の予約投稿
- 予約の一覧、詳細、編集、論理削除
- 定期予約の一時停止と再開
- 本文なしのdraftとcreator DM通知
- operator channel、operator DM、logへの通知fallback
- PostgreSQLへの永続化とDBポーリング
- 一時エラーの再試行、Rate Limit対応、結果不明時の二重投稿防止
- 起動時Recovery
- 終端予約と関連履歴の30日保持・cleanup
- 日本語のDiscord Embed表示
- 予約ID Autocomplete
- `/post show`の通常Autocompleteから削除済み予約を除外（canonical UUID直接入力と削除済み一覧からの参照は可能）
- 最大32文字の手動予約名、AIなしの決定的フォールバック名、一覧・詳細・候補への安全な名前表示
- 予約詳細Viewからの編集、一時停止、再開、削除

AIによる予約名生成、AI文章生成、文体の保存・反映、PAY.JP、サブスクリプション管理、複数guild、Web管理画面、添付ファイル、月次・年次予約、カレンダーUIは将来構想です。現在利用できる機能ではありません。

詳細は[要件](docs/requirements-beta.md)、[技術設計](docs/technical-design-beta.md)、[運用Runbook](docs/operations.md)、[開発・公開ロードマップ](docs/development-roadmap.md)、[Phase 1手動受入](docs/manual-acceptance-phase1.md)、[Phase 2手動受入](docs/manual-acceptance-phase2.md)、[Phase 3受入](docs/manual-acceptance-phase3.md)を参照してください。

## Phase 2完了後の方針

現在はローカル環境で開発を継続し、一般公開の準備が整うまで常時稼働環境を構築しません。機能実装、自動テスト、文書、ポートフォリオを先に完成させ、公開直前に少人数・短期間の限定テストを実施します。合格後は無料の常時稼働環境を第一候補として配置し、不足する場合だけ有料環境を検討します。

AI予約名は未実装の任意機能です。初期状態では無効とし、AIが無効、利用上限到達、timeout、異常応答の場合も予約投稿の基本機能を継続する方針です。詳細とPhase 3の実装順序は[開発・公開ロードマップ](docs/development-roadmap.md)を参照してください。

## 必要環境

- WSL2またはLinux
- 通常版CPython `>=3.14,<3.15`
- Docker EngineとDocker Compose
- Composeで使用するPostgreSQL `18.4-bookworm`
- Discord applicationとBot
- OAuth2 scopes: `bot`、`applications.commands`
- Bot権限: 対象チャンネルの閲覧、メッセージ送信、Embed Links
- Privileged Intents: 不要（Members、Presences、Message Contentは無効）
- Administrator権限: 不要

DM通知はDiscordユーザーのDM受信設定にも依存します。DMできない場合はoperator通知へfallbackします。

## 初回セットアップ

以下はリポジトリルートで実行します。

```bash
cd ~/projects/discord-ai-reminder-bot
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
cp .env.example .env
git check-ignore .env
```

`.env`をエディターで設定します。内容を画面やログへ表示せず、本物のtokenや本番資格情報を`.env.example`やコマンド履歴へ書かないでください。`git check-ignore .env`が`.env`を出力すればignore対象です。

Compose定義を読み取り専用で確認し、開発用PostgreSQLだけを起動します。

```bash
docker compose config
docker compose up -d postgres
docker compose ps
python -m discord_ai_reminder_bot.infrastructure.database.health
```

Schemaを明示的に適用・確認します。Botは自動Migrationしません。

```bash
alembic upgrade head
alembic current
alembic check
```

通常テストと静的検査を実行します。

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

Botを起動します。

```bash
python -m discord_ai_reminder_bot
```

起動時にDB revisionが単一のAlembic headと一致しなければ、BotはDiscordの通常処理を開始しません。

## 日常の作業開始

リポジトリルートで次の順に実行します。

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

## 日常の作業終了

1. Botを実行しているターミナルで`Ctrl+C`を1回押します。
2. 投稿、通知、maintenanceの3 loopとRecovery Task、確認View、Discord Client、DB Engineの終了を待ち、プロセスが終了したことを確認します。
3. リポジトリルートで開発用PostgreSQLだけを停止します。

```bash
docker compose stop postgres
docker compose ps
git status --short --branch
```

通常停止では名前付きVolume `postgres_data`を維持します。プロジェクト全体やVolumeをまとめて削除するCompose操作は使用しません。

## PostgreSQL統合テスト

統合テストは開発DBとは別の`postgres_test`を使います。リポジトリルートで実行してください。

```bash
docker compose --profile test up -d postgres_test
docker compose --profile test ps
DATABASE_URL="${TEST_DATABASE_URL:?TEST_DATABASE_URLをテストDBへ設定してください}" alembic upgrade head
TEST_DATABASE_URL="${TEST_DATABASE_URL:?TEST_DATABASE_URLをテストDBへ設定してください}" python -m pytest tests/integration
docker compose --profile test stop postgres_test
docker compose --profile test rm -f postgres_test
```

`TEST_DATABASE_URL`は`.env.example`のローカルテスト専用接続先を安全に設定し、値を表示しないでください。本番資格情報をコマンド履歴に残してはいけません。停止・削除対象は`postgres_test`だけです。開発用`postgres`と`postgres_data`には触れず、プロジェクト全体やVolumeをまとめて削除するCompose操作は使用しません。

## `/post`コマンド

`/post show`または`/post list`から開いた予約詳細では、状態に応じて編集・一時停止・再開・削除を実行できます。詳細の「✏️ 編集」は画面を確認しながら編集する一般向け経路です。単発・毎日・毎週に対応したModalで、投稿先、日時・繰り返し条件、本文をまとめて変更でき、本文や終了日の空欄入力で設定を解除できます。一覧から開いた場合は元の絞り込みとページへ戻れます。

- `/post create`: 単発予約
- `/post create-daily`: 毎日予約（終了日は `明日`、`8/30`、`2026-08-30` など）
- `/post create-weekly`: 毎週予約
- `/post list`: 操作可能な予約の一覧（全件数、ページ移動、予約種類の絞り込み、選択詳細）
- `/post show`: 予約詳細
- `/post edit`: 予約IDを候補から選び、1項目以上を直接指定する短縮・上級者向け編集（一時停止中も既存の許可項目を編集可能。定期の `local_time` は基本投稿時刻の恒久変更で、今回だけの時刻変更には使用しない）
- `/post delete`: 確認View付き論理削除。管理者が同じサーバーの他作成者予約を削除する場合だけ、前後空白除去後1～500文字の監査理由が必須（空白だけは拒否）
- `/post pause`: 定期予約の一時停止
- `/post resume`: 定期予約の再開

`show`、`edit`、`delete`、`pause`、`resume`では予約ID欄に投稿先チャンネル名（例: `tester`、`#一般`、`お知らせ`）を入力すると、権限と操作可能状態に応じた候補を最大25件表示します。完全一致・前方一致・部分一致に対応し、英字の大文字小文字は区別しません。候補値は完全なUUIDv7です。UUID、種別、状態、数値チャンネルIDでも検索でき、候補を選ばず完全な予約IDを直接貼り付けても実行できます。

日時形式、状態遷移、権限、通知、Recoveryの詳細は[Phase 1要件](docs/requirements-beta.md)を参照してください。

## 機密情報

`.env`はGit管理対象外です。Discord Bot token、DB資格情報、接続URL、投稿本文をGit、Issue、チャット、通常ログへ出さないでください。運用上の予約識別には内部DB IDではなく、Discordに表示される完全なUUIDv7を使用します。
