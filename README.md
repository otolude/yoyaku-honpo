# Discord AI Reminder Bot

Discordへの予約投稿、AIによる文章作成、利用者の文体反映、サブスクリプション管理を行うBotです。

## 現在の段階

Phase 1の要件と技術設計を確定し、開発環境と設定基盤を構築しています。Discord Bot本体、DBモデル、予約処理はまだ未実装です。

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
