# Discord AI Reminder Bot

Discordへの予約投稿、AIによる文章作成、利用者の文体反映、サブスクリプション管理を行うBotです。

## 現在の段階

開発環境の準備中です。機能要件と技術構成は次の工程で確定します。

## 開発環境

- Windows 11
- WSL2（Ubuntu）
- Python 3.14
- Git / GitHub

## セットアップ

```bash
cd ~/projects/discord-ai-reminder-bot
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

## 動作確認

```bash
pytest
```

## 機密情報について

Discord、AIサービス、PAY.JPなどの秘密鍵はGitへコミットしません。ローカルでは `.env` を使用し、共有用の見本だけを `.env.example` に記載します。

