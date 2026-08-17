# Discord AI Reminder Bot

Discordへの予約投稿、AIによる文章作成、利用者の文体反映、サブスクリプション管理を行うBotです。

## 現在の段階

Phase 1の要件と技術設計を確定し、DBモデル、予約ドメイン、基本Repositoryを構築しています。Discord Bot本体、コマンド、予約ワーカーはまだ未実装です。

## 開発環境

- Windows 11
- WSL2（Ubuntu）
- 通常版CPython 3.14
- Docker Desktop（WSL 2 integrationを有効化）
- PostgreSQL 18.4（Docker Composeで起動）
- Git / GitHub

## セットアップ

### 1. Python仮想環境と依存関係

```bash
cd ~/projects/discord-ai-reminder-bot
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

BotはDockerコンテナではなく、このWSL上の `.venv` で実行します。

### 2. 環境変数

共有用の見本をコピーして、ローカル専用の `.env` を作ります。

```bash
cp .env.example .env
```

`.env`をエディターで開き、Discord IDなどを設定してください。本物のBotトークンはBot接続を実装・確認する段階まで空欄で構いません。ただし、設定読込の動作確認では必須値がすべて必要です。

`.env`はGitの管理対象外です。トークンや本番のパスワードを `.env.example` へ書かないでください。

### 3. PostgreSQL

Docker Desktopを起動し、WSL 2 integrationが有効であることを確認してから実行します。

```bash
docker compose up -d postgres
docker compose ps
```

停止するときは次を実行します。通常の停止ではデータは名前付きVolumeに残ります。

```bash
docker compose stop postgres
```

PostgreSQLだけがコンテナで起動します。Phase 1のBotはコンテナ化しません。

### 4. DB接続確認

PostgreSQLを起動し、`.env`に `DATABASE_URL`だけを設定してから次を実行します。Discord BotトークンやDiscord IDは、この接続確認には必要ありません。

```bash
python -m discord_ai_reminder_bot.infrastructure.database.health
```

このコマンドは読み取り専用の `SELECT 1` だけを実行します。テーブルの作成やデータ更新は行いません。

### 5. Alembic

Alembicは、SQLAlchemyのモデルに合わせてPostgreSQLのテーブル構造を段階的に変更し、その履歴を管理するツールです。

マイグレーションはBot起動とは別に、開発者またはデプロイ処理が明示的に適用します。Bot起動時に自動適用はしません。

```bash
alembic upgrade head
```

DB接続URLは `alembic.ini` へ書かず、`.env`の `DATABASE_URL`から読み込みます。

### 6. PostgreSQL統合テスト

統合テストは、開発DBとは別の一時的なPostgreSQLを使用します。`test` profileを指定して起動してください。

```bash
docker compose --profile test up -d postgres_test
docker compose --profile test ps
```

テストDBは`127.0.0.1:55432/discord_bot_test`です。データはtmpfsへ保存されるため、コンテナを削除すると消えます。開発DBのVolumeは共有しません。

初回リビジョンをテストDBだけへ適用します。

```bash
export TEST_DATABASE_URL='postgresql+psycopg://discord_bot_test:discord_bot_test_password@127.0.0.1:55432/discord_bot_test'
DATABASE_URL="$TEST_DATABASE_URL" alembic upgrade head
```

統合テストは`TEST_DATABASE_URL`がある場合だけ動きます。接続前にローカルホストとテスト用DB名を検証し、`discord_bot_dev`や本番らしい接続先を拒否します。

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" python -m pytest tests/integration
```

停止して一時データを破棄する場合は次を実行します。この2つのコマンドは `postgres_test` だけを停止・削除し、コンテナのtmpfs上にあるテストデータも削除します。開発用の `postgres` サービスは停止せず、`postgres_data` Volumeも削除しません。開発データを巻き込む可能性があるため、`docker compose down` や `docker compose down -v` は使用しないでください。

```bash
docker compose --profile test stop postgres_test
docker compose --profile test rm -f postgres_test
```

## 動作確認

```bash
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

Composeファイルの内容は、コンテナを起動せずに確認できます。

```bash
docker compose config
```

## 機密情報について

Discord、AIサービス、PAY.JPなどの秘密鍵はGitへコミットしません。ローカルでは `.env` を使用し、共有用の見本だけを `.env.example` に記載します。
