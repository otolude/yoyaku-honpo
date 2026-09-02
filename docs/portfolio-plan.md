# ポートフォリオ掲載計画

## 1. 文書の位置づけ

本書はPhase 3ロードマップ第6項「ポートフォリオを整備する」の6A成果物であり、掲載要件、匿名化基準、6B成果物、6C監査範囲を定義する。6B-1ではREADMEと文章・Mermaid成果物を作成し、6B-2の画面資料と6C監査は分離する。

基準はidentity書換え後commit `bd2c9be91e8ddeb936a4b37888099d649b177375`時点である。Phase 1は63／63、Phase 2は47／47、Phase 3は6A項目追加前で99／101であり、実OpenAI Provider受入とARM64 Linux実機確認は未確認である。これはtree内容上の文書基準であり、過去の実行証跡を新commitで再実行したという意味ではない。数値は品質を恒久保証するものではなく、その時点の受入記録への索引として扱う。

## 2. 目的と対象読者

製品の採用予定名は「よやく本舗」、英字表記は「Yoyaku Honpo」とする。日本語で使いやすいDiscordの予約投稿・リマインダーBotとして、日本語UI、JST、単発・毎日・毎週の予約投稿という実装済み範囲を紹介する。現在のAI実装は予約名生成のProvider非依存基盤に限定され、初期無効かつ実Provider未検証である。より広いAI予約支援は将来構想として分離し、現在利用できる機能とは表現しない。本製品は独立開発であり、Discordの公式・公認・提携製品と誤認させない。

ポートフォリオの目的は、GitHubで公開し、就職活動での企業提示とクラウドワークス等の案件応募において、第三者が実装範囲、設計判断、安全境界、検証状況を短時間で確認できるようにすることである。対象読者は採用担当者、技術担当者、発注者とする。将来の正式リリースを目指す製品開発であることを示しつつ、ローカル開発、隔離テスト、実Discord確認、公開前確認、本番運用を混同しない。

GitHub repositoryは公開前に`yoyaku-honpo`へ改名済みだが、現在もPrivateで公開前準備中である。public化時期は6C監査合格後を候補とし、改名や6B文書整備だけで公開可否を確定しない。GitHubプロフィールREADME、pin留め、連絡導線、クラウドワークス掲載文は後続作業とし、個人情報、mail、SNS、案件媒体URLを本repositoryへ追加しない。

対象読者と最初に提示する情報は次のとおりとする。

| 対象読者 | 最初に必要な情報 | 詳細への導線 |
| --- | --- | --- |
| 採用担当者・案件担当者 | 課題、主要機能、現在地、代表画面、技術要約 | READMEから機能・検証文書へリンク |
| エンジニア | レイヤー分離、transaction、Worker、Recovery、情報境界 | architecture、feature-flows、security-and-privacy |
| 運用・セキュリティ確認者 | 秘密管理、DB分離、Migration安全性、未確認事項 | security-and-privacy、verification、operations |
| 将来の協力者 | 開発環境、検証入口、未実装範囲、公開準備状況 | README、verification、roadmap |

READMEは短時間で全体像を把握する入口とし、長い設計理由、全状態遷移、全テスト証跡、詳細Runbookは重複掲載せず既存文書または`docs/portfolio/`へ分離する。

## 3. 言語方針

| 案 | 利点 | 負担・リスク | 判定 |
| --- | --- | --- | --- |
| 日本語のみ | 現在のUI・文書・主対象読者と一致し、保守負担が最小 | 英語話者が概要を把握しにくい | 採用しない |
| 日本語本文＋短いEnglish summary | 日本語の正確さを維持し、海外の技術者にも目的・状態・主要技術を短く示せる | 短い概要の同期確認が必要 | 採用 |
| 日英完全併記 | 幅広い読者へ同じ詳細を提供できる | READMEと詳細文書が倍増し、更新漏れ・意味差のリスクが高い | 現段階では採用しない |

主要言語は日本語とし、6BでREADME冒頭に短いEnglish summaryを置く。英語概要は目的、主要機能、主要技術、ローカル開発中で未公開、AI Provider未稼働という現在地だけを扱う。詳細文書の日英完全併記は、具体的な応募先または利用者需要が判明した場合の将来候補とする。

## 4. 実装状態の表記ルール

掲載する機能・主張には次のいずれかの状態を付け、異なる証跡を一つの「完成」にまとめない。

| 表記 | 意味 |
| --- | --- |
| 実装済み | 現在のコードまたはMigrationに機能が存在する |
| 自動隔離テスト済み | Fake、Mock、実ViewStoreまたは専用PostgreSQLで対象境界を直接検証済み |
| 実Discord確認済み | 受入文書に実施日・実施者・証跡が記録された実Discord確認 |
| 設計・文書化のみ | 要件または設計は確定しているが、対応コードはない |
| 延期中 | 実施予定を維持したまま、明示的な後続ゲートへ移した |
| 未実装 | 現在のコードに存在しない |
| 将来計画 | 商品仕様・公開環境等の判断後に実施候補となる |

自動テスト件数だけを品質保証、SLA、可用性、セキュリティ認証の根拠にしない。実Discord確認済みであっても一般公開、本番運用、実利用者利用の証拠にはしない。

## 5. 掲載可能な実装範囲

次の分類は現行コード、Migration、受入文書から直接説明できる範囲である。6Bでは対応する詳細文書または受入証跡へリンクし、表記状態を省略しない。

| 項目 | 掲載状態 | 根拠・制限 |
| --- | --- | --- |
| Discord予約投稿、単発・毎日・毎週 | 実装済み、実Discord確認済み | Phase 1受入。単一設定guildのローカル開発 |
| 一覧・詳細・編集・一時停止・再開・論理削除 | 実装済み、実Discord確認済み | Phase 1・2受入。状態別操作境界あり |
| owner／administrator／guild／DM認可境界 | 実装済み、自動隔離テスト済み、一部実Discord確認済み | 最新Interactionで再認可。一般公開実績ではない |
| Autocomplete | 実装済み、実Discord確認済み | 最大25件、安定順、操作別状態。show通常候補からdeletedを除外 |
| 長寿命ViewとModal lifecycle | 実装済み、自動隔離テスト済み、実Discord確認済み | nonce付き外側Modal、timeout、二重submit、Bot close回収 |
| PostgreSQL、SQLAlchemy、Alembic | 実装済み、自動隔離テスト済み | Migration head `a41f8c7d2e90`。本番DB運用実績ではない |
| 投稿Worker、通知Worker、retry、Recovery、cleanup | 実装済み、自動隔離テスト済み、一部実Discord確認済み | 投稿retryとAIの再試行なしを混同しない |
| OperationLog | 実装済み、自動隔離テスト済み | 名前等の情報境界を含む。監査認証を意味しない |
| 手動予約名、JSTフォールバック名 | 実装済み、自動隔離テスト済み、実Discord確認済み | フォールバックはDBへ保存せず本文を候補へ出さない |
| AI予約名Job／Budget／Worker基盤 | 実装済み、自動隔離テスト済み | Provider無効が初期値。顧客Quotaではない |
| OpenAI Adapter | 実装済み、実SDK＋Mock transportで自動隔離テスト済み | 実API品質・費用・保持は未確認で、正式Provider採用前 |
| Provider受入安全CLI | 実装済み、無通信の自動隔離テスト済み | 通常READMEにはdry-runだけ掲載可能。liveは延期中 |
| Migration接続先安全ラッパー | 実装済み、自動隔離テスト済み | test／development／productionをfail-closedで分離 |
| pytest、Ruff、Migration検証 | 実施証跡あり | 数値には実施時点とcommitを併記し、最新性を再確認する |
| 情報境界、秘密情報非露出、AllowedMentions | 実装・テスト証跡あり | 認証取得や完全な安全性保証とは表現しない |
| 正式リリース時のサブスクリプション | 設計・文書化のみ | 導入は正式リリース要件。商品・決済実装はない |

## 6. 非掲載または注意が必要な主張

次を現在の実績として記載しない。

- OpenAI Providerを正式採用済み、またはAI予約名の実API品質を確認済み。
- AI APIを本番運用中、実利用者データで検証済み、実費を伴う運用実績がある。
- ARM64 Linux対応確認済み。
- 24時間常時稼働、一般公開、本番環境構築、実利用者による利用、SLAまたは可用性実績。
- サブスクリプション販売、PAY.JP正式採用、決済、Plan、Entitlement、顧客Quotaの実装。
- ゼロコスト運用、販売価格確定、恒久的なAI上限確定。
- セキュリティ認証、脆弱性が存在しないこと、法令・規約への完全適合。
- pytest件数のみを根拠とする品質保証。

未確認の実Provider受入とARM64 Linux実機確認は、READMEの現在地とverificationの未確認欄へ明記する。

## 7. 6B成果物と情報配置

6Bでは次を作成する。既存文書を正本とし、ポートフォリオ文書には第三者向けの要約と索引だけを置く。

| 成果物 | 内容 | 正本・重複回避 |
| --- | --- | --- |
| `README.md` | 概要、解決する課題、主要機能、代表画像、技術要約、安全設計、検証概要、最短セットアップ入口、現在地、ロードマップ | 詳細仕様を複製せず下記文書と既存文書へリンク |
| `docs/portfolio/architecture.md` | レイヤー、実行コンポーネント、DB・外部境界、将来境界 | DB列定義や全transaction詳細は技術設計を正本とする |
| `docs/portfolio/feature-flows.md` | 作成・投稿・通知・Recovery・予約名・Migrationの主要フローと状態遷移 | 全例外条件は要件・技術設計へリンク |
| `docs/portfolio/security-and-privacy.md` | 認可、情報最小化、秘密管理、AI・Discord境界、既知の限界 | 運用手順はoperationsを正本とする |
| `docs/portfolio/verification.md` | 受入区分、最新検証時点、commit、コマンド入口、未確認事項 | Phase別受入文書を証跡の正本とする |
| `docs/portfolio/screenshot-policy.md` | 撮影、匿名化、レビュー、差替え、削除基準 | 本書の基準を作業チェックリストへ具体化 |
| `docs/portfolio/assets/manifest.md` | 各assetの由来、合成条件、匿名化方法、確認日、掲載先 | 秘密・実ID・元画像へのリンクを含めない |
| `docs/portfolio/assets/` | 匿名化済み画像だけ | 元画像、編集レイヤー、EXIF付き原本は含めない |

代表画像はREADMEの理解を助ける最小枚数にし、残りは詳細文書へ分離する。Mermaid等のテキスト図を優先し、画像化した図を重複管理しない。badgeは実際に公開・継続実行されるCI、ライセンス、配布状態だけに限定し、6B時点で根拠がなければ追加しない。

## 8. 画像・asset匿名化基準

追跡済みの画像・スクリーンショット・diagramは現在存在しない。過去の実Discord受入画像は出所、写り込み、metadata、再利用許可をGit上で検証できないため、6Bでは使用しない。専用開発guildへ合成データを用意して新規撮影することを必須とし、撮影前匿名化を後編集より優先する。

撮影・掲載時は次を満たす。

- 実利用者データを使わず、架空のBot名、利用者名、予約名、本文、channel名だけを使う。
- Discord user ID、guild ID、channel ID、message ID、実public UUID、実サーバー名、実URLを露出しない。
- UUIDが必要なら文書用と明示した架空値を使い、UIが実値を表示する場合は掲載範囲から除外するか復元不能に焼き込む。
- avatar、username、サーバー名、channel名、サイドバー、DM、通知、他メッセージを撮影前後に確認し、不要領域を切り取る。
- PC時刻、ホスト名、OS user名、ローカルパス、terminal prompt、履歴を含めない。
- `.env`、接続URL、APIキー、token、cookie、Project ID、Organization ID、支払い情報を含めない。
- metadataを除去し、再読込してEXIF等がないことを確認する。
- ファイル名とalt textにも実ID、秘密、実本文、個人情報を含めない。
- マスクは完全不透明かつ復元不能な焼き込み形式とし、半透明塗り、別レイヤー、CSSだけの隠蔽に依存しない。
- 元画像、編集可能レイヤー、撮影途中の画像をGitへ含めない。
- `manifest.md`へasset path、合成データであること、撮影元の種類、匿名化方法、metadata確認日、確認者、掲載先を記録する。実guild名や内部IDは記録しない。

Discordの名称、UI、商標・ブランド利用条件は6Cまでの調査対象とし、Discord公式画面または公式提携を誤認させる表現、ロゴ改変、不要なロゴ掲載を避ける。法的適合を本書だけで断定しない。

採用予定名に関して、利用者提供のJ-PlatPat検索結果では「よやく本舗」「予約本舗」「Yoyaku Honpo」は各0件、「ヨヤクホンポ」の称呼類似検索は2件であった。その内訳として、登録5453553「よやくーぽん！」（第42類、類似群コード42P02・42X11）と登録6836621「ヨヤクーポン」（第35・36類）は登録存続中と報告されている。これは利用者が行った検索の記録であり、法的な使用可否の判断、網羅的な類似調査、商標クリアランス完了を意味しない。「よやく本舗」は採用予定として管理し、公開前に専門家への相談要否を含む商標確認を行う。この記録だけで6Cまたは公開受入を完了にしない。

2026-09-02に利用者は、「よやく本舗」を採用予定名・商標確認未完了と明記してソース閲覧用portfolioへ掲載する方針を承認した。少人数の無償closed test前は可能であれば商標専門家へ相談し、有償test、広告、契約、一般提供の開始前には商標確認を必須の判断事項とする。検索0件や今回の掲載判断を、商標上の安全性、非侵害、登録可能性の表明または保証として扱わない。

## 9. 構成図・フロー図の安全境界

構成図にはDiscord Interaction、Bot Application、Domain／Application／Infrastructure、PostgreSQL、Schedule worker、Notification worker、Name generation worker、NameGenerator Port、Disabled／OpenAI Adapter、外部AI Provider候補、将来Entitlement境界、Migration安全ラッパー、cleanup／Recoveryを掲載できる。

実host名、IP、port、実DB名、DB user、password、接続URL、container ID、worker UUID、Project ID、Organization ID、APIキー、内部canary、Schedule UUID、Discord IDは掲載しない。構成要素名は論理名とし、サンプル値を実値に見せない。

現在の接続は実線、初期無効または選択可能なAdapterは状態ラベル付き、未実装のEntitlement、顧客Quota、ModelSelectionPolicy、決済、公開環境は破線と「未実装」ラベルで表す。OpenAI Adapterの存在を外部API稼働中と誤認させない。

## 10. 再現手順の基準

READMEには次の最短入口だけを置く。

1. 対応環境がWSL2／Linux、CPython `>=3.14,<3.15`、Docker、Composeであること。
2. 仮想環境を作成し、`python -m pip install -e '.[dev]'`で依存を導入すること。
3. `.env.example`を基にローカル`.env`を作り、秘密を表示・commitしないこと。
4. 開発用PostgreSQLを個別に起動し、Migration安全ラッパーでdevelopment targetと期待DB名を明示すること。
5. `python -m pytest`、Ruff check、Ruff format checkの入口。
6. `python -m discord_ai_reminder_bot`によるBot起動入口。

接続値、Discord設定、バックアップ、障害対応、停止、専用test DB、Migrationの操作別confirmationは[運用Runbook](operations.md)へ委ねる。開発DBとtest DBを明確に分け、Alembic CLI直接実行、URLのshellコピー、広範なDocker削除、Volume削除、破壊的DB操作をREADMEのコピー可能な通常手順にしない。

OpenAIは初期disabledと明記する。手動Provider受入CLIは`--dry-run`だけを通常READMEへ掲載でき、live confirmation、APIキー設定、課金・購入手順は掲載しない。Discord設定は必要scope・権限の概要に留め、tokenや実IDを例示しない。WSL2 x86_64での確認とARM64 Linux未確認を区別する。

## 11. テスト・受入証跡の掲載基準

READMEにはPhase 1 63／63、Phase 2 47／47、Phase 3の最新集計と未確認項目を簡潔に掲載する。詳細は`verification.md`から各Phase受入文書へリンクする。自動テスト、専用PostgreSQL統合テスト、実ViewStore／Fake Interaction、実Discord、人手運用確認を別区分にする。

最新の通常pytest、PostgreSQL込みpytest、Ruff、Migration検証は、6Bまたは6Cで実際に再確認したcommit、実施日、環境とともに掲載する。固定件数を自動更新できない間は「記録時点の値」であることと、コード変更で古くなることを明記する。READMEへ過去の複数件数を並べず、最新概要とverificationへのリンクだけを置く。

テストで保証できないものとして、一般公開時の負荷・可用性、実Provider品質・保持・請求、ARM64 Linux、第三者によるセキュリティ監査、法令・商標適合、利用者体験、SLAを明示する。

## 12. ライセンス・公開準備監査

6A時点のローカル監査結果は次のとおりである。

| 項目 | 状態 | 6B／6Cで必要な判断 |
| --- | --- | --- |
| LICENSE／COPYING | オープンソースライセンスは付与せず、`COPYRIGHT.md`で独自部分をAll rights reservedとしている | 第三者由来部分は`THIRD_PARTY_NOTICES.md`で分離し、wheel等の配布前監査は別途実施 |
| CONTRIBUTING | 追加済み・外部PRを積極募集しない方針 | 公開後の受付方針を変更する場合に再確認 |
| SECURITY.md | 追加済み・Private vulnerability reportingへ案内 | public化直前に同機能を手動有効化し、実際の公開状態を確認 |
| Code of Conduct | ファイルなし | community運用方針に応じて要否を決定 |
| Issue／PR template | bug／improvement Issue Form追加済み・PR templateなし | 公開後の受付範囲を変更する場合に再確認 |
| GitHub Actions／CI | 6C-1でworkflow追加・初回成功確認済み | 対象commitと確認日をverificationへ記録し、badgeは実workflowだけを参照 |
| dependency license | ソース閲覧公開範囲を監査済み・Alembic個別MIT notice対応済み | wheel等の実配布物に含まれるdependencyとNOTICEを配布前に再監査 |
| Discord／OpenAIの名称・画面・ロゴ | 公式条件を監査し、限定掲載を利用者承認済み | 非提携表示を維持し、条件更新時と一般提供前に再確認 |
| 採用予定名「よやく本舗」 | portfolio掲載承認済み・商標確認未完了 | closed test前は可能なら専門家相談、有償test・広告・契約・一般提供前は確認必須 |
| 第三者素材 | Alembic由来templateを個別noticeで管理し、Discord UI画像4枚の限定掲載を承認済み | 追加assetごとに出典、ライセンス、改変可否をmanifestまたは第三者noticeで管理 |
| 個人情報・著作権 | 合成データ、画像、Git履歴を人手監査済み | 追加素材・追加撮影ごとに同じ基準で再確認 |
| repository name／visibility | `yoyaku-honpo`へ改名済み・Private | public化時期と最終公開可否を利用者が判断 |
| Git履歴の秘密情報 | identity書換え後の到達履歴を監査済み | public化直前の差分と追加履歴を再確認 |
| `.env`・一時ファイル | `.gitignore`あり、`.env.example`だけ追跡・6C監査済み | public化直前のtracked／untracked差分を再確認 |

法的判断は断定せず、「確認済み」「未確認」「利用者判断が必要」を維持する。独自部分の利用条件と第三者由来部分のライセンスを混同せず、将来の配布形態に応じて再監査する。

## 13. 6B作業項目

6Bは次の2段階に分ける。

- 6B-1: README、利用者承認済みEnglish summary、architecture、feature flows、security and privacy、verification、screenshot policy、asset manifest、Mermaid、実装状態matrix、安全なsetup入口を作成する。
- 6B-2: 本書とscreenshot policyに従って合成データ画像4枚を新規撮影し、alt textとmanifestの実recordを対応付ける。（2026-08-30完了。全画像・Git履歴・商標・公開状態の最終監査は6C）

1. READMEを短い入口へ再設計し、日本語本文と短いEnglish summaryを作る。
2. 実装済み／未実装／延期中マトリクスを作り、正本へリンクする。
3. `docs/portfolio/`の5詳細文書とasset manifestを作る。
4. 秘密を含まないMermaid構成図、主要機能フロー、状態遷移を作る。
5. 専用開発guildと合成データで代表画面を新規撮影し、匿名化・metadata除去・manifest記録を行う。
6. READMEの最短再現入口とoperationsの詳細手順を整合させる。
7. verificationへ検証時点、commit、環境、結果、限界、未確認2件を記録する。
8. ライセンス、SECURITY、CONTRIBUTING等は利用者判断を得たものだけ追加する。

## 14. 6C監査項目

1. README、詳細文書、図、asset、manifest間のリンクと正本を確認する。
2. 実装状態の各主張をコード、Migration、テスト、受入記録へ対応付ける。
3. 実Provider、ARM64、公開、本番、契約・決済を完了扱いしていないことを確認する。
4. 全画像を目視し、実ID、個人情報、通知、端末情報、秘密、metadata、復元可能なマスクがないことを確認する。
5. Git追跡対象と履歴を、値を出力しない方法で秘密情報監査する。
6. 構成図に実インフラ識別情報がなく、未実装境界が破線・ラベルで区別されていることを確認する。
7. READMEの再現手順を新しい隔離環境で確認し、開発DB・test DB境界と破壊的操作非掲載を確認する。
8. 最新検証結果の時点、commit、環境、限界を確認する。
9. dependency license、採用LICENSE、第三者素材、Discord／OpenAIの商標・画面条件を確認する。
10. repository visibility、SECURITY、CONTRIBUTING、行動規範、template、CI・badgeの利用者判断を記録する。
11. Markdown、alt text、見出し、表、リンク、秘密情報、差分範囲を機械・目視確認する。

## 15. 6A完了条件と未確定事項

6A文書作成の完了条件は、目的・読者、掲載・非掲載、状態表記、成果物、匿名化、図、再現手順、証跡、法務、6B、6Cの要件が本書に揃い、Phase 3受入表へ機械集計可能な項目として追加されることである。要件定義だけで直接確認できる条件は文書差分監査後に6A隔離受入済みとし、成果物への準拠、最終監査、利用者判断を必要とする条件は6B／6Cへ分離して未確認のまま維持する。

### 15.1 初回16条件の再分類

pytest nodeは6Aの文書要件そのものを検証しないため、A項目に対応するpytest node IDはない。直接証跡は本書の該当節、README、roadmap、requirements、technical design、operations、Phase 3受入表の文書差分とする。Git証跡は、identity書換え後の基準commit `bd2c9be91e8ddeb936a4b37888099d649b177375`から変更したものがこの7文書だけで、Phase 1・Phase 2受入文書、Python、テスト、DBモデル、Migration、`.env`に差分がなく、画像が追跡されていない状態である。書換え時に旧新tree OID一致を確認しており、このSHA更新は文書基準の移行であって過去検証の再実行記録ではない。6B／6Cの実物が必要な条件は既存テストを代替証跡にしない。

| No. | 元の条件 | 分類 | 直接証跡・判定 |
| --- | --- | --- | --- |
| 1 | 目的・対象読者 | A | 本書2節の目的、4読者と情報導線 |
| 2 | 掲載・非掲載と状態区分 | A | 本書4～6節、requirementsの掲載要件 |
| 3 | 復元不能な匿名化方式 | A | 本書8節の焼き込み・半透明等の禁止基準 |
| 4 | 実利用者データ・実IDを使わない | A | 本書8節、operations 22節 |
| 5 | 元画像・編集レイヤー等を追跡しない | A | 本書8節の非追跡基準。現時点で追跡画像なし |
| 6 | asset manifest要件 | A | 本書7～8節の項目・非記録情報 |
| 7 | 構成図の秘密境界・将来表示 | A | 本書9節、technical design 24.5節 |
| 8 | 再現手順のDB・Migration安全境界 | A | 本書10節、README／operationsの役割分担 |
| 9 | AI初期disabled・live手順非掲載 | A | 本書10節、requirementsとoperationsの境界 |
| 10 | テスト証跡の時点・限界 | A | 本書4節・11節 |
| 11 | 6B成果物・6C監査・正本関係 | A | 本書7節・13～14節 |
| 12 | 実Provider・ARM64未確認を隠さない | A | 本書1節・6節・11節、Phase 3受入表 |
| 13 | 6Bで新規合成データを撮影 | B | 2026-08-30に6B-2で4画像を作成・組み込み済み |
| 14 | 全assetの写り込み・metadata等を確認 | E | 6B-2の各asset監査は完了。Git履歴・商標・公開状態を含む最終監査はCへ分割 |
| 15 | LICENSE・依存・商標・履歴監査 | C | 現在はいずれも未完了。6Cで実査が必要 |
| 16 | visibility・公開準備ファイル・CI判断 | E | 現状確認はC、採否・連絡先・公開時期は利用者判断Dへ分割 |

Aの12件は本書と関連文書の整合、ローカルリンク、Markdown、Git差分範囲、秘密情報候補の隔離監査を直接証跡として確認できる。B、C、Dを含む条件は、方針が記載されていても成果物や判断が存在しないため確認済みにしない。

未確定事項は次のとおりである。

- 採用するLICENSEと著作権表示。
- repositoryをpublicにする時期と現在のvisibility。
- CONTRIBUTING、SECURITY、Code of Conduct、Issue／PR template、CIの要否と運用主体。
- Discord／OpenAIの名称、画面、ロゴに関する最新条件。
- 6Bで撮影する具体的な画面、架空名、撮影担当者、確認担当者。
- 英語詳細文書が必要となる具体的な応募先・利用者需要。
- 実Provider受入とARM64 Linux実機確認の実施時期。

## 16. 参考資料の採用判断

次の2記事はGitHubポートフォリオの見せ方を考える参考資料としてのみ使用する。記事の文章やtemplateは転載せず、本repositoryの正式仕様、コード、受入結果、利用者指示、法的判断の根拠にはしない。

- [ポートフォリオに関する参考記事](https://freelance-concierge.jp/articles/detail/437/)
- [ポートフォリオ用のGitHubリポジトリに関する参考記事](https://qiita.com/miruky/items/726d9210cc8666866f86)

採用する考え方は、冒頭で価値を伝える、技術stackを明示する、画面資料への導線を後から追加する、安全なsetup入口を設ける、architectureとdirectory責務を説明する、課題・設計判断・test・error handling・文書化を示す、完成度の高い少数repositoryを優先することである。

GitHub Pages、live demo、PR template、完全な日英併記、個人連絡先掲載は現段階では採用しない。著作権方針、GitHub Actions、bug／improvement Issue Form、CI badgeは6C-1の利用者判断と成功証跡に基づいて追加した。継続的な開発履歴は実際のcommitと文書で示し、毎日commitすること自体を目的にしない。

## 17. repository完成後の応募導線

6Cと公開判断後の別作業として、GitHub profile READMEへ得意領域と学習・開発中技術を整理し、本repositoryをpin留めする。クラウドワークス等のportfolio欄からGitHubへリンクし、必要に応じてQiita／Zennへ原因調査や設計判断を匿名化してまとめる。氏名、mail、SNS、案件媒体URL、稼働条件を本書から推測して追加せず、連絡導線は利用者が別途決定する。
## 16. 6C-1 リポジトリ運営基盤

利用者判断により、オープンソースライセンスを付与しないCopyright Noticeと`Copyright (c) 2026 Oto. All rights reserved.`、Security Policy、Contribution方針、bug／improvement Issue Form、blank Issue無効化、GitHub Actions CIを採用する。公開目的は就職活動・案件応募・ポートフォリオ評価とし、GitHub利用規約上の閲覧・forkを除く独自コード・文書の利用は明示的な書面許可を必要とする。Code of ConductとPR templateは追加せず、外部PRは積極募集しない。Issueは受け付け、成功確認済みworkflowのCI badgeを掲載する。

GitHub上のCI成功は2026-08-31に利用者が確認済みである。repositoryはPrivateのまま`yoyaku-honpo`へ改名済みで、現行のbadgeとSecurity Policy導線は新repository URLを使用する。2026-09-02にソース閲覧用portfolioの依存license、第三者素材、商標・画面掲載条件に関する利用者判断を記録済みであり、Private vulnerability reportingの有効化、repository visibility、最終public化は6Cへ残す。公開は6C合格後の利用者承認による手動操作とし、GitHub profile等の導線整備も別作業とする。
