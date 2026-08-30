# 検証スナップショット

## 1. 記録の読み方

本書は2026-08-30時点の検証記録への索引である。固定件数は対象コードcommitのスナップショットで、現在の最新実行結果、品質保証、SLA、外部監査を意味しない。自動テスト、専用PostgreSQL統合、実ViewStore／Fake Interaction、実Discord、人手運用確認を区別する。

## 2. コード基準と環境

| 項目 | 記録値 |
| --- | --- |
| 対象コードcommit | `79f7d79ddb92769b3cfa2406bb8c62143d271849` |
| 検証日 | 2026-08-30 |
| Python | 3.14.4 |
| OS／architecture | WSL2 Linux x86_64 |
| OpenAI Python SDK | 2.54.0 |
| httpx | 0.28.1 |
| Alembic head | `a41f8c7d2e90`、single head |

対象commitは2C-2の手動Provider受入安全基盤を含む。後続の`bd2c9be91e8ddeb936a4b37888099d649b177375`と`24fe5df14eeafb0ae870686ae87c94ad11cfa609`はポートフォリオ計画の文書commitで、Python、DB model、Migrationを変更していない。本6B-1も文書変更だけであり、下記件数を再実行したものではない。

## 3. 自動検証の記録

| 区分 | 結果 | 境界 |
| --- | --- | --- |
| 通常pytest | 1,019 passed／349 skipped | 専用DBがない場合のunit、Fake Interaction、実ViewStore等 |
| PostgreSQL込み全pytest | 1,368 passed | 専用test DBでRepository、transaction、制約、並行境界を含む |
| 2C-2重点 | 52 passed | Provider受入CLIのguard、費用、無通信、cancel、close |
| 2C-1回帰込み重点 | 180 passed | Adapter、実SDK＋Mock transport、Worker境界 |
| Ruff check | 成功 | 対象コードcommitの静的検査 |
| Ruff format check | 成功 | 対象コードcommitのformat検査 |
| pip check | 成功 | 上記Python環境の依存整合性 |
| Alembic current／heads／check | 成功 | 正式Migration安全ラッパー経由 |
| test DB終了時 | 既存6表＋AI Job／Budget 2表が0件 | 専用test DBの清掃確認 |

OpenAI Adapterのcontract testは実SDKとMock transportを使用し、実OpenAI APIへ接続していない。実API品質、費用、保持、請求、dashboard設定の受入ではない。

## 4. Phase受入

| Phase | 状態 | 主な証跡 |
| --- | --- | --- |
| Phase 1 | 63／63 | [Phase 1受入](../manual-acceptance-phase1.md) |
| Phase 2 | 47／47 | [Phase 2受入](../manual-acceptance-phase2.md) |
| Phase 3 | 6C-1ローカル隔離受入後120／126 | [Phase 3受入](../manual-acceptance-phase3.md)を最新集計の正本とする |

Phase 1・2には自動テストと実Discord／隔離環境の人手受入が含まれる。Phase 3では自動隔離受入、実ViewStore、専用PostgreSQL、実Discordの証跡を項目ごとに記録している。件数だけで未列挙の動作を保証しない。

## 5. 未確認・未実装

- 実OpenAI Providerでの日本語品質、応答時間、token、費用、保持、請求、dashboard設定
- ARM64 Linux実機での依存解決、import、Mock transport、shutdown
- 24時間常時稼働、一般公開、実利用者利用、本番監視
- Plan、Entitlement、顧客Quota、契約、決済、Webhook
- LICENSE、dependency license、商標・画面掲載条件、Git履歴の公開前最終監査

## 6. 更新手順

コードまたは依存関係を変更した場合は、対象commitと環境を固定して通常pytest、専用PostgreSQL込みpytest、Ruff、pip check、正式Migrationラッパーによるcurrent／heads／checkを再実行する。終了時の専用DB清掃と専用container停止を確認し、新しい日付、commit、件数で本書とREADME概要を更新する。

文書だけの変更では過去の検証を再実行したように記載せず、コード基準との関係を明示する。実Discord、実Provider、ARM64、隔離セットアップはそれぞれ別の人手受入として記録する。
## 7. 6C-1検証の扱い

6C-1ではCopyright Notice、Security、Contribution、Issue Form、CI workflowをローカルで静的・自動検証した。2026-08-31にOtoがGitHub画面で、commit `7d47ae36d2e47aa6f74d0bc583e4d2181d82b660`のActions run #2、実行時間1分8秒、test job成功、Artifactsなし、Node.js 20 deprecated警告なしを確認した。workflowは`actions/checkout@v7`と`actions/setup-python@v7`を使用する。

旧著作権方針版を参照したActions run #1は利用者がGitHub画面から削除し、Actions一覧にはrun #2だけを残した。repositoryはPrivateでpublic化前、Private vulnerability reportingは未有効である。CI成功は依存license、第三者素材・商標、Git履歴、visibility、公開可否、実Provider、ARM64の確認を代替しない。著作権方針はオープンソースライセンスを付与しないCopyright Noticeを正本とする。
