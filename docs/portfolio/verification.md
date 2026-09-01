# 検証スナップショット

## 1. 記録の読み方

本書は2026-09-02時点の検証記録への索引である。固定件数は対象コードcommitのスナップショットで、現在の最新実行結果、品質保証、SLA、外部監査を意味しない。自動テスト、専用PostgreSQL統合、実ViewStore／Fake Interaction、実Discord、利用者によるGitHub画面確認を区別する。identity書換え前に取得した実行証跡は、旧新tree一致を確認しても新commitで再実行した結果とは扱わない。

## 2. コード基準と環境

| 項目 | 記録値 |
| --- | --- |
| 対象コード | identity書換え前の非公開対応commit（書換え後の内容同一commitは`79f7d79ddb92769b3cfa2406bb8c62143d271849`） |
| 検証日 | 2026-08-30 |
| Python | 3.14.4 |
| OS／architecture | WSL2 Linux x86_64 |
| OpenAI Python SDK | 2.54.0 |
| httpx | 0.28.1 |
| Alembic head | `a41f8c7d2e90`、single head |

対象は2C-2の手動Provider受入安全基盤を含むidentity書換え前の対応commitである。後続の書換え後commit `bd2c9be91e8ddeb936a4b37888099d649b177375`と`24fe5df14eeafb0ae870686ae87c94ad11cfa609`はポートフォリオ計画の文書commitで、Python、DB model、Migrationを変更していない。本6B-1も文書変更だけであり、下記件数を再実行したものではない。完全な旧新対応はGit管理外の非公開mappingで保持する。

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
| Phase 3 | Git履歴・画像・秘密情報最終監査後123／127 | [Phase 3受入](../manual-acceptance-phase3.md)を最新集計の正本とする |

Phase 1・2には自動テストと実Discord／隔離環境の人手受入が含まれる。Phase 3では自動隔離受入、実ViewStore、専用PostgreSQL、実Discordの証跡を項目ごとに記録している。件数だけで未列挙の動作を保証しない。

## 5. 未確認・未実装

- 実OpenAI Providerでの日本語品質、応答時間、token、費用、保持、請求、dashboard設定
- ARM64 Linux実機での依存解決、import、Mock transport、shutdown
- 24時間常時稼働、一般公開、実利用者利用、本番監視
- Plan、Entitlement、顧客Quota、契約、決済、Webhook
- LICENSE、dependency license、第三者素材、商標・画面掲載条件、repository visibility、最終公開判断
- 採用予定名「よやく本舗」の商標クリアランス（利用者提供のJ-PlatPat初期検索記録だけでは未完了）

## 6. 更新手順

コードまたは依存関係を変更した場合は、対象commitと環境を固定して通常pytest、専用PostgreSQL込みpytest、Ruff、pip check、正式Migrationラッパーによるcurrent／heads／checkを再実行する。終了時の専用DB清掃と専用container停止を確認し、新しい日付、commit、件数で本書とREADME概要を更新する。

文書だけの変更では過去の検証を再実行したように記載せず、コード基準との関係を明示する。実Discord、実Provider、ARM64、隔離セットアップはそれぞれ別の人手受入として記録する。
## 7. 6C-1検証の扱い

6C-1ではCopyright Notice、Security、Contribution、Issue Form、CI workflowをローカルで静的・自動検証した。2026-08-31にOtoがGitHub画面で、identity書換え前の対応commitのActions run #2、実行時間1分8秒、test job成功、Artifactsなし、Node.js 20 deprecated警告なしを確認した。workflowは`actions/checkout@v7`と`actions/setup-python@v7`を使用する。

旧著作権方針版を参照したActions run #1は利用者がGitHub画面から削除し、Actions一覧にはrun #2だけを残した。repositoryはPrivateでpublic化前、Private vulnerability reportingは未有効である。CI成功は依存license、第三者素材・商標、visibility、公開可否、実Provider、ARM64の確認を代替しない。著作権方針はオープンソースライセンスを付与しないCopyright Noticeを正本とする。

2026-09-01には利用者がGitHub画面で、identity書換え前の対応commitのworkflow実行とtest jobの成功、新しい警告なし、Artifactsなし、READMEのCI badgeからworkflow画面へ移動可能であることを確認した。run番号や所要時間は提供されていないため記録しない。これは過去の利用者画面確認であり、次節以降のローカル隔離検証や新履歴の証跡とは別の区分である。

2026-09-02には利用者がidentity書換え後のdevelop `0d3b0a5956b61a7a1cdd30126f5ad3d3caf163b1`について、Status Success、test job成功、新しい警告なし、Artifactsなし、READMEのCI badgeから新workflowへ移動可能であることと、commitがGitHub accountへ関連付いていることを画面確認した。run番号と所要時間は提供されていないため推測しない。localではauthor／committer nameがOto、emailがGitHub noreply形式であることを値を掲載せず確認した。

## 8. 2026-08-31 DB非依存隔離検証

この節はidentity書換え前の対応commitだけを対象とする保存証跡の事後監査記録であり、現在の作業ツリーや書換え後commitの検証結果ではない。書換え時に対応するtree OIDが一致したことは確認したが、新commitで再実行したとは扱わない。証跡は2026-08-31にLinux x86_64（WSL2）、CPython 3.14.4、pip 25.1.1、build 1.6.0で取得された。新規venvを作り、`env -i`で環境を制限し、公開PyPIから依存を解決したうえで、テスト時はOS sandboxが外向きsocket作成を`EPERM`で拒否した。filesystem namespace自体は分離していない。

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

## 9. 2026-09-01 PostgreSQL internal network隔離検証

この節はidentity書換え前の対応commitだけを対象とするローカル隔離検証である。書換え時に対応するtree OIDが一致したことは確認したが、新commitで再実行したとは扱わない。WSL2 Linux x86_64、Python 3.14.4、pip 25.1.1、pytest 9.1.1、`postgres:18.4-bookworm`を使用した。対象commitから追跡176ファイルを展開してGit blob不一致0件を確認し、runner内のsource manifest、前回保存した44行の依存一覧、wrapper hashがbuild contextと一致し、pip checkが成功した。

runnerはUID／GID 1000:1000の非root、read-only root filesystem、`cap_drop: ALL`、`no-new-privileges:true`、privileged falseで、Docker socket、host source、既存`.env`・`.venv`・Volumeをmountしなかった。runnerのNetworkModeがpostgres_testのcontainer IDを指し、network namespace inodeも一致した。PostgreSQL側だけをinternal bridgeへ接続し、公開portとnamed Volumeを持たず、DB保存先をtmpfsとした。runnerからは`127.0.0.1:5432`の専用DBだけに接続でき、公開IPv4／IPv6への直接接続は`ENETUNREACH`、bridge gatewayの5432／55432／2375／80／443とloopback 55432は接続拒否だった。`host.docker.internal`と外部hostnameは解決せず、既存開発・撮影containerは停止中で専用network endpointを持たなかった。

PostgreSQL healthとアプリケーションhealthの`SELECT 1`が成功し、正式Migrationラッパーによる`upgrade head`、`current`、`heads`、`check`が成功した。`a41f8c7d2e90`のsingle headで差分はなかった。主要8表はMigration直後、integration終了後、停止直前の3時点ですべて0件だった。通常pytestは1,019 passed／349 skippedでwarningなし、349件は`TEST_DATABASE_URL`を渡さないPostgreSQL integrationのskipである。別processのintegrationは349 passed／0 skipped／0 errorsでwarningなし、算術合計は1,368 passedとなった。通常・integrationログの合成secret完全一致は0件で、runnerとpostgres_testはともに`Exited (0)`だった。

永続証跡はGit管理外に保存し、通常ファイル229件、rootを含むdirectory 24件、symlink 0件、rootを含む全entry 253件、`evidence` 38ファイルである。通常ファイルmanifestは`24662c3587e3050e3c28207bab9c4b0958c316ee38ed686f683cc2f5a631a685`、directory sizeをfilesystem非依存に正規化したinventoryは`029255b2d26034b571b5c9542ba55318667c162f6ec113e0777eeb7b75620456`、evidence manifestは`ce587756d73a18c7103285ace38b861887b668c4be797b1304292ae08154762f`と再照合した。保存先は公開用再現手順や配布物ではなく、repositoryへ追加しない。

この検証はDocker Desktop daemon、WSL2 kernel、管理者侵害への耐性を保証しない。build段階は公開PyPIへ接続し、runnerとDBはnetwork namespaceを共有した。test DBはtmpfsのため停止後には残らず、停止前ログを証跡とする。lock fileがないため将来も同じ依存が解決される保証はない。永続証跡には0600の専用secretファイルとして合成値が残るが実credentialではなく、値を文書へ転記しない。ARM64、実Provider、法的配布可否、最終公開判断、安全性は未確認または保証対象外である。

## 10. 2026-09-02 identity書換えとGit履歴・画像・秘密情報監査

mainとdevelopから到達可能だった83 commitのauthor／committerを、同一mappingでOto＋repository設定済みGitHub noreplyへ統一した。両branchは完全一致leaseを指定した単一のatomic pushで更新した。旧新83組についてtree OID、commit messageのbyte hash、author date、committer date、parent mappingが全件一致し、到達可能blob集合も旧新とも708件で完全一致した。rootは1件、mergeとsigned commitは0件で、書換えによるGit管理ファイルの内容変更はない。完全なmappingと旧履歴bundleはGit管理外の非公開証跡として保持し、path、identity値、旧commitの完全SHAを公開文書へ掲載しない。

書換え後のmain／developを読み取り再監査し、83 commit、708 blob、author／committer identity 1種類、旧個人名・旧Gmail一致0件を確認した。token、API key、credential、秘密鍵、実`.env`、実DB URL等の確定漏えいと高確度secret候補はなかった。専用secret scannerは未導入のため、Git object展開、既知形式の高確度pattern、path・内容の分類、画像構造・画素・目視検査を組み合わせた。

現行4枚と差替え前4枚の計8 PNG blobはidentity書換え前に監査したSHA-256と全件一致した。各blobでPNG signature、全chunk CRC、IEND、trailing dataなし、metadata chunkなしを確認し、全画素のalphaは255、黒矩形は完全不透明だった。目視上、利用者名、実サーバー名、sidebar、DM、通知一覧、実IDはなく、到達履歴にも匿名化前画像、編集layer、生成前画像はなかった。ただし完成PNGだけから編集前データの不存在を絶対保証せず、GitHub内部object、cache、旧Actions run、既存cloneからの旧履歴完全回収も保証しない。

新main／developはlocalとoriginで一致し、mainはdevelopの祖先、tagはない。旧main、旧develop、旧MIT commitはbranch・tagから到達不能である。元cloneのreflog、非公開bundle、一時bare repository、GitHub内部保持には旧objectが残り得るため、通常の公開refからの非到達と完全消去を区別する。
