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

OpenAI Responses API向けの予約名Adapterは2C-1の隔離実装まで存在しますが、ProviderとAIは初期無効で、APIキー未設定、実Provider受入未実施のため外部AI機能はまだ利用できません。通常候補は`gpt-5.6-luna`、品質優先の比較候補は`gpt-5.4-nano-2026-03-17`で、実API限定比較前のためどちらも正式採用済みではありません。Deprecatedの`gpt-5-nano`は新規採用しません。AI文章生成、複数guild、Web管理画面、添付ファイル、月次・年次予約、カレンダーUIは後続開発で扱う未実装機能です。

正式リリースではサブスクリプション契約を導入することを要件としますが、現段階では決済機能、料金プラン、契約管理、Webhookを実装しておらず、ローカル開発・ポートフォリオ段階で課金しません。PAY.JPは過去からの決済Provider候補であり、正式採用済みではありません。利用者の文体学習、過去投稿学習、Embedding、利用者プロフィール生成は採用しません。

詳細は[要件](docs/requirements-beta.md)、[技術設計](docs/technical-design-beta.md)、[運用Runbook](docs/operations.md)、[開発・公開ロードマップ](docs/development-roadmap.md)、[Phase 1手動受入](docs/manual-acceptance-phase1.md)、[Phase 2手動受入](docs/manual-acceptance-phase2.md)、[Phase 3受入](docs/manual-acceptance-phase3.md)を参照してください。

## Phase 2完了後の方針

現在はローカル環境で開発を継続し、一般公開の準備が整うまで常時稼働環境を構築しません。機能実装、自動テスト、文書、ポートフォリオを先に完成させ、公開直前に少人数・短期間の限定テストを実施します。合格後は無料の常時稼働環境を第一候補として配置し、不足する場合だけ有料環境を検討します。

AI予約名のProvider非依存基盤は2B-2まで実装し、2C-1ではOpenAI固有処理を`infrastructure` Adapterへ隔離しました。永続Jobのclaim、運営Budget予約、DB資源を閉じたGenerator実行、CAS finalize、Recovery・poll・shutdown・cleanup接続を含みます。初期設定はProvider／AIとも無効で、APIキーがなくても既存Botは起動できます。AIが無効、利用上限到達、timeout、異常応答の場合も、JSTフォールバック名、手動名編集、予約作成・投稿を継続します。

公開前限定テストで決済を確認する場合は、選定したProviderのsandbox／test modeだけを使用します。料金、プラン名、無料枠、試用期間、契約単位の最終形、決済Providerは商品仕様監査後に確定し、推測で補いません。現在の50回／日、500回／月、100円相当／月は、Provider未選定・未接続の開発段階と2Bの初期実装・隔離テストで使う変更可能な費用安全値です。利用者向け販売価格でも正式リリース時の恒久上限でもなく、2CのProvider選定時とサブスクリプション商品設計時に再計算します。

正式リリース後は、AI API、常時稼働サーバー、DB・ストレージ、バックアップ、ログ・監視、ネットワーク、決済手数料、税・返金・障害対応の予備費など、必要な運用費をサブスクリプション収益で賄います。利用者が実用上問題なく使える品質、回数、応答速度を確保したうえで、重複呼び出し、無制限再試行、不要な長文・過剰出力、不要な高価格モデル、無期限保存を避けます。コスト削減によって通常利用を困難にしたり、頻繁にフォールバックへ落としたりする設計にはしません。

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
python -m discord_ai_reminder_bot.infrastructure.database.migrate --target development --expected-database discord_bot_dev --confirm development:discord_bot_dev:upgrade upgrade head
python -m discord_ai_reminder_bot.infrastructure.database.migrate --target development --expected-database discord_bot_dev current
python -m discord_ai_reminder_bot.infrastructure.database.migrate heads
python -m discord_ai_reminder_bot.infrastructure.database.migrate --target development --expected-database discord_bot_dev check
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
python -m discord_ai_reminder_bot.infrastructure.database.migrate --target development --expected-database discord_bot_dev current
python -m discord_ai_reminder_bot.infrastructure.database.migrate heads
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
python -m discord_ai_reminder_bot.infrastructure.database.migrate --target test --expected-database discord_bot_test --confirm test:discord_bot_test:upgrade upgrade head
TEST_DATABASE_URL="${TEST_DATABASE_URL:?TEST_DATABASE_URLをテストDBへ設定してください}" python -m pytest tests/integration
docker compose --profile test stop postgres_test
docker compose --profile test rm -f postgres_test
```

Migrationラッパーが読む`TEST_DATABASE_URL`は実行プロセスの環境へ安全に設定し、値を表示したりコマンド引数へ載せたりしないでください。ラッパーはtestで`DATABASE_URL`や`.env`へfallbackしません。本番資格情報をコマンド履歴に残してはいけません。停止・削除対象は`postgres_test`だけです。開発用`postgres`と`postgres_data`には触れず、プロジェクト全体やVolumeをまとめて削除するCompose操作は使用しません。

Migrationの正式経路は`python -m discord_ai_reminder_bot.infrastructure.database.migrate`だけです。Alembic CLIの直接実行とoffline SQL生成は禁止します。`current`と`check`にもtargetと期待DB名が必要で、`upgrade`、`downgrade`、`stamp`、`autogenerate`は`target:database:operation`と完全一致する確認値がなければ接続前に拒否されます。productionは実DB名を明示できる運用環境が確定するまで実行できません。

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
