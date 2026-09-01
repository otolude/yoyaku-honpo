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
- 採用予定名「よやく本舗」の商標クリアランス（利用者提供のJ-PlatPat初期検索記録だけでは未完了）

## 6. 更新手順

コードまたは依存関係を変更した場合は、対象commitと環境を固定して通常pytest、専用PostgreSQL込みpytest、Ruff、pip check、正式Migrationラッパーによるcurrent／heads／checkを再実行する。終了時の専用DB清掃と専用container停止を確認し、新しい日付、commit、件数で本書とREADME概要を更新する。

文書だけの変更では過去の検証を再実行したように記載せず、コード基準との関係を明示する。実Discord、実Provider、ARM64、隔離セットアップはそれぞれ別の人手受入として記録する。
## 7. 6C-1検証の扱い

6C-1ではCopyright Notice、Security、Contribution、Issue Form、CI workflowをローカルで静的・自動検証した。2026-08-31にOtoがGitHub画面で、commit `7d47ae36d2e47aa6f74d0bc583e4d2181d82b660`のActions run #2、実行時間1分8秒、test job成功、Artifactsなし、Node.js 20 deprecated警告なしを確認した。workflowは`actions/checkout@v7`と`actions/setup-python@v7`を使用する。

旧著作権方針版を参照したActions run #1は利用者がGitHub画面から削除し、Actions一覧にはrun #2だけを残した。repositoryはPrivateでpublic化前、Private vulnerability reportingは未有効である。CI成功は依存license、第三者素材・商標、Git履歴、visibility、公開可否、実Provider、ARM64の確認を代替しない。著作権方針はオープンソースライセンスを付与しないCopyright Noticeを正本とする。

## 8. 2026-08-31 DB非依存隔離検証

この節はcommit `a5e21b7511e9a1aed6805cb25448ca2fa7697e86`だけを対象とする保存証跡の事後監査記録であり、現在の作業ツリーや後続commitの検証結果ではない。証跡は2026-08-31にLinux x86_64（WSL2）、CPython 3.14.4、pip 25.1.1、build 1.6.0で取得された。新規venvを作り、`env -i`で環境を制限し、公開PyPIから依存を解決したうえで、テスト時はOS sandboxが外向きsocket作成を`EPERM`で拒否した。filesystem namespace自体は分離していない。

保存証跡は通常ファイル7,899件、directory 1,252件、symlink 5件、全entry 9,156件で、root directoryはdirectory数と全entry数に含めない。全通常ファイルの相対path順SHA-256 manifestは`d471b04f17ce5270e996e487b3748f178d95093ad071503fc7854185ac04a9f2`、保存時inventoryは`12c8c6992812b3a1c6645b641cec2db81dbecba78410c0b643455d125a1c773c`と一致した。対象commitから展開した追跡176ファイルは全件でGit blob hashが一致した。証跡そのものはrepositoryへ追加しない。

| 区分 | 結果 |
| --- | --- |
| 依存install | 成功。lock fileはなく、将来も同一versionが解決される保証はない |
| build isolation | sdist用とsdist由来wheel用を確認。Hatchling 1.32.0、packaging 26.3、pathspec 1.1.1、pluggy 1.6.0、tomlkit 0.15.1、trove-classifiers 2026.6.1.19 |
| sdist／wheel | 両方成功。wheelはsdistから作成 |
| pip check／Ruff | pip check、Ruff check、Ruff format checkが成功 |
| DB非依存pytest | 1,019 passed、skipなし |
| 通常pytest | 1,019 passed／349 skipped。全skipは`TEST_DATABASE_URL`未設定によるPostgreSQL統合test |

wheelは76 memberで`RECORD`整合、危険なarchive path、symlink、疑わしい名前、資格情報URLを検出しなかった。ただし`COPYRIGHT.md`を収録せず、`License-Expression`と`License` metadataはいずれも未設定だった。sdistは追跡176ファイルと`PKG-INFO`の177 memberで、追跡ファイルの欠落・余分な未追跡ファイルがなく、`COPYRIGHT.md`を収録したが、同じくlicense metadataは未設定だった。sdistの資格情報URL候補1件は統合テスト内の固定ダミー値であり、実資格情報ではない。両artifactに危険なarchive path、秘密情報、実`.env`、DB、backupの混入は確認されなかった。

build frontendは一時build用venvを削除したため、build依存versionはfrontend出力、pip log、専用cacheのwheelで確認しており、削除済みsite-packagesの実体は事後検査できていない。artifactは配布せず、wheelの著作権表示不足やlicense metadata未設定を推測で補正しない。この証跡はDB統合、ARM64、実Provider、GitHub Actions、法的な配布可否、一般公開可能性、安全性を確認または保証しない。
