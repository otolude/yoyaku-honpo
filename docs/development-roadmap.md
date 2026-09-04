# Phase 2完了後の開発・公開ロードマップ

## 1. 現在地と開発方針

Phase 1とPhase 2は受入完了済みである。現在はローカル環境で開発を継続し、一般公開の準備が整うまで常時稼働環境を構築しない。長期間のベータ運用は必須とせず、機能実装、自動テスト、文書、ポートフォリオを先に完成させる。

開発、公開前限定テスト、本番ではDiscord Application、PostgreSQLデータベース、秘密情報を分離する。クラウド固有機能への依存を抑え、Dockerを使って配置先を移行できる構成を維持する。

## 1.1 Phase 4: AI投稿本文下書き

Phase 3の受入結果（確認済み125件／未確認2件、合計127件、および第6項6Cの4件／4件）は変更せず、AI投稿本文下書きは別のPhase 4として管理する。Phase 4Aでは要件、Discord画面遷移、Provider情報境界、保持、費用、安全性、受入条件だけを文書化し、Python実装、Migration、DB操作、Bot操作、実Provider通信、実Discord受入、ARM64 Linux実機受入は行わない。

Phase 4は、4A文書化、4B Provider非依存Domain／Application、4C rate limit／永続Budget、4D Provider Adapter、4E `/post compose` UI、4F自動隔離・PostgreSQL統合、4G実Provider受入、4H実Discord・ARM64受入の順で進める。

現在はProvider非依存境界、本文専用Usage schema／Repository／cleanup／Settings、無効Composition、production未接続のOpenAI Responses API Adapter、UI Session／ControllerとDiscord UI部品、`PostDraftRuntime`を実装し、既存guild限定`/post` Groupへ`/post compose`をコード上で登録した。commit `cf34dac4ca7d2f65ebfbcc2d1c16a7e36e777c90`の隔離runtime受入は完了しているが、Provider gateはfalseで、AI buttonは「AIで作成（準備中）」のdisabled状態である。実Discord command sync／画面受入、実Provider、AI有効end-to-end、予約保存・投稿、Usage cleanupのruntime定期実行、ARM64 Linux実機は未実施で、正式model、価格・費用承認、正式UI timeoutも未決定である。

本文生成feature flagは初期無効とし、実Provider・実Discord・ARM64 Linux実機受入がすべて完了するまで有効化しない。Phase 4の受入は[AI投稿本文下書き受入表](manual-acceptance-ai-post-drafting.md)で管理する。

## 2. Phase 3ロードマップ

Phase 3は次の順序で進める。

1. `/post show`の通常Autocomplete候補から削除済み予約を除外する。（実装・実Discord受入完了）
2. AI予約名機能の要件、安全境界、DB設計を確定する。（2A、2B-1／2B-2、2C-1のOpenAI Adapter隔離実装、2C-2の手動受入安全基盤まで完了。実Provider受入とARM64 Linux実機確認は公開前へ延期）
3. AIを使わない場合の予約名フォールバックを実装する。（実装・受入完了）
4. 予約詳細画面から予約名を編集できるようにする。（実装・受入完了）
5. 一覧、詳細、Autocomplete、確認画面へ予約名を表示する。（実装・受入完了）
6. ポートフォリオを整備する。（6A、6B-1、6B-2、6Cの4／4件とソース閲覧用portfolioの最終公開受入まで完了）
7. 公開前限定テストを実施する。
8. 常時稼働環境を構築し、本番リリースする。

Phase 3の受入は[Phase 3受入表](manual-acceptance-phase3.md)でPhase 1・Phase 2と分離して管理する。第1段階、2A、2B-1、2B-2、2C-1、2C-2の安全基盤は隔離受入済みである。実Provider受入ではProvider、価格、品質、保持、請求、dashboard条件を再確認する。

次に着手する正式段階は、第6項「ポートフォリオを整備する」とする。実Provider受入とARM64 Linux実機確認は中止せず、公開前限定テストおよび配置環境確定時の未確認ゲートとして残すため、ポートフォリオ整備の開始条件にはしない。

ポートフォリオ整備の目的は、実装済みの予約機能、情報境界、AIのfail-closed設計、テスト証跡を、秘密情報や実利用者データを含めず第三者が確認できる形へ整理することである。この段階ではREADME・構成図・機能説明・匿名化した画面資料・再現可能な検証手順を整合させる。予約機能やAI Providerの追加実装、実API通信、公開前Discord試験、常時稼働環境、DBモデル／Migration、Plan／Entitlement／Quota、契約・決済は対象外とする。

実装順は、6Aで掲載要件を確定し、6B-1でREADMEと文書・Mermaid成果物、6B-2で匿名化画像4枚を作成した。最後に6Cでリンク、隔離再現、Git履歴・画像、ライセンス・商標、公開状態、実装主張と受入の整合を監査する。6Aの正本は[ポートフォリオ掲載計画](portfolio-plan.md)、6B成果物は[`docs/portfolio/`](portfolio/architecture.md)と[asset manifest](portfolio/assets/manifest.md)を参照する。外部サービスや有料処理は使用せず、DBモデルとMigrationを変更しない。

Public repositoryは就職活動で企業へ提示し、クラウドワークス等の案件応募でも発注者への技術説明に使用する予定である。対象読者は採用担当者、技術担当者、発注者とする。2026-09-02に匿名／ログアウト状態の公開ページ、画像、Issue／Security導線を確認し、Private vulnerability reportingがEnabledであることを含めて6Cの最終公開受入を完了した。GitHubプロフィールREADME、pin留め、連絡導線、案件媒体の掲載文は後続作業とする。

2B-2後の運用安全修正として、Migrationの正式経路をPythonラッパーへ統一し、`alembic/env.py`にもtarget、期待DB名、操作確認、接続後`current_database()`の最終ガードを追加する。test／development／productionのURL選択を分離し、直接Alembic CLI、offline mode、接続先不一致をDDL前に拒否する。

## 3. AI予約名の境界

AIの用途は、現在の投稿本文から最大32文字の短い予約名を1件生成することだけに限定する。生成名では改行と制御文字を禁止する。

利用者ごとの文体・文脈・過去投稿を学習する機能、利用者プロファイル、過去投稿検索、Embedding、ベクトルDB、ファインチューニング、恒久的な学習データセットは作成しない。AI入力とAI応答の履歴も保存しない。

予約名のsourceは次の3値とする。

- `ai`: AIが生成した名前
- `manual`: 利用者が編集した名前
- `unset`: 保存された名前がない状態

`manual`は本文変更後も維持し、AIが自動で上書きしない。`ai`の状態で本文が変更された場合は古いAI名を解除して再生成を1回だけ試し、失敗時は`unset`として古いAI名を残さない。AI生成名は親Scheduleの一部として保持し、親Scheduleと同時に削除する。

## 4. 非AIフォールバック

非AIフォールバック名はDBへ保存せず、予約の既存情報から表示時に決定的に構築する。

- 単発: `単発予約 M/D HH:MM`
- 毎日: `毎日予約 HH:MM`
- 毎週: `毎週予約 曜日 HH:MM`
- 必要な日時がない場合: `名称未設定`

本文の一部をフォールバック名として使用せず、一覧やAutocompleteへ本文を表示しない。AIが無効、上限到達、timeout、異常応答の場合も、このフォールバックにより基本機能を継続する。

## 5. AI費用と呼び出し制御

AIは初期状態で無効とし、明示設定時だけ有効化する。同じSchedule versionにつき最大1回、timeoutは5秒、自動再試行は行わない。

50回／日、500回／月、100円相当／月は、Provider未選定・未接続の開発段階における2B初期実装・隔離テスト用の変更可能な設定値である。利用者向け販売価格や正式リリース時の恒久上限ではなく、コード定数やDBのCHECK制約へ変更不能な値として固定しない。2CのProvider選定時とサブスクリプション商品設計時に再計算する。運営者全体のBudgetと将来の顧客プランQuotaは別Policy・別集計として維持する。

2B-1ではProvider SDKや外部通信を導入せず、永続Job、JSTのBudget bucket、変更可能な`BudgetPolicy`、作成・本文編集時の冪等登録、保守的CAS保存、起動時Recoveryと保持期限cleanupのApplication基盤までを実装する。2B-2で初めてpoll loop、transaction外Generator呼び出し、Bot startup／shutdown接続を実装する。

2B-2ではclaimと悲観Budget予約を1 transactionでcommitし、Session、transaction、row lock、ORMをGeneratorへ渡さず、別の短いtransactionでCAS finalizeする。startup recovery後だけ5秒間隔・1件・最大並行1のpollを開始し、shutdownではGenerator taskをcancel・回収して`shutdown_unknown`へ終端化する。本番DIはDisabledのままであり、Fakeは隔離テスト専用である。

2C-1では条件付き第一候補をOpenAI APIとし、statelessなResponses API Adapter、許可モデル・価格・為替・入出力上限のfail-closed設定、構造化出力の再検証、SDK retry 0、SDK clientのshutdown回収を無通信Fakeで隔離実装する。通常候補は日付snapshotが公開されていない`gpt-5.6-luna` alias、品質比較候補は固定`gpt-5.4-nano-2026-03-17`とする。Deprecatedの`gpt-5-nano`は拒否する。どちらも実API限定比較前で正式採用済みではない。

2C-2では、通常Bot・Worker・pytest・DBから分離した手動受入CLIを実装する。固定6件の匿名合成case、許可モデル、request数、悲観最大費用、完全一致confirmation、process専用キー、公式endpoint、redirect／proxy無効を境界とし、引数なし・help・dry-runでは通信しない。安全基盤の自動隔離受入だけを完了扱いとし、実API品質、保持、請求、dashboard、ARM64は別の未確認受入として残す。

実Provider受入は公開前へ延期する。専用Projectは準備済みだが、内部識別情報は文書化しない。Project上の比較候補は`gpt-5.6-luna`と`gpt-5.4-nano`だけで、各モデルの上限は60,000 TPM、10 RPMである。残高0 USD、支払い方法未登録、APIキー未作成、API通信0回、費用発生なしの状態を維持し、Project作成をProvider正式採用の証跡にはしない。

課金可能な状態を作る前に利用者の明示許可を再取得する。最低プリペイド購入が必要になる可能性がある場合も、購入額を実試験のAPI原価と混同せず、Auto-rechargeを無効にしてから専用Project、制限付きキー、Project予算・アラートを確認する。固定匿名6 caseをLunaへ6回、別runでGPT-5.4 nano固定snapshotへ6回実行し、両runとも各requestの間隔を60秒以上空ける。retry、fallback、Batch、並列実行、自動保存は行わない。悲観費用はLuna 333,600 JPY microunits／回、GPT-5.4 nano 334,200 JPY microunits／回、合計4,006,800 JPY microunits（約4.0068円）であり、プリペイド購入額や販売価格ではない。日本語品質、32文字、応答時間、token、請求、保持、dashboard設定を確認後にモデルの使い分けを決め、プラン別モデル・回数・機能は商品仕様策定時に確定する。

将来の商品仕様では`AI設定 → Entitlement → 顧客プランQuota → ModelSelectionPolicy → 運営Budget → Job → Generator`の順を採用候補とする。2C-1では運営設定による単一モデルだけを扱い、Entitlement、顧客Quota、ModelSelectionPolicy、契約、決済を実装しない。基本プランでLunaと少なめの枠、上位プランでGPT-5.4 nanoと多めの枠・追加AI機能を提供する案は未確定であり、品質差が小さければ全プラン共通Lunaとして回数・機能だけを分ける選択肢も維持する。

不正値、未設定、価格不明、集計失敗、回数または費用上限到達時はAIを呼ばない。AI失敗や一時的な上限到達でも基本予約機能を継続する。一方、正式リリースでは利用者が実用上問題なく使える品質、回数、応答速度を先に確保し、通常利用で頻繁にフォールバックへ落ちるほど低い上限を採用しない。そのうえで重複呼び出し、無制限再試行、不要な長文入力・過剰出力、不要な高価格モデル、無期限保存を避ける。

正式リリース前に、AI Providerの入出力単価、1 guildあたりの平均・上位利用量、販売価格、プラン別AI利用枠、サーバー・DB・ストレージ・バックアップ・監視・ネットワーク費、決済手数料、税・返金・障害対応の予備費、目標原価率、想定外利用への余裕、実用上必要な回数と応答品質からBudgetとQuotaを再計算する。リリース後も利用状況、原価、解約率、障害率を確認してプランと上限を見直せるようにする。

一覧、詳細、Autocomplete、投稿Worker、Recovery、通知WorkerからAIを呼ばない。AI無効、上限到達、timeout、異常応答でも、予約の作成、編集、投稿を失敗させない。

## 6. AIデータとプライバシー

AIへ送信するのは、その1回の予約名生成に必要な現在の投稿本文と固定生成条件だけとする。Discord guild ID、利用者ID、チャンネルID、予約ID、内部DB ID、過去投稿、履歴など不要な識別情報を送信しない。

AI入力本文をアプリケーション側の別履歴として保存しない。APIキー、本文、AI入力全文、AI応答全文を通常ログへ記録しない。AI呼び出し中にDB Session、transaction、row lockを保持しない。外部AIの用途、送信先、保存方針は利用者へ明示し、本文中の命令をAIのシステム指示として扱わない。文体学習、過去投稿学習、Embedding、利用者プロフィール、無期限保存を採用しない目的は、費用だけでなく、個人情報と投稿データの最小化、不要なプロファイリングの防止、漏えい時の影響軽減、利用者へ説明しやすく削除依頼へ対応しやすい仕組み、過度な複雑化の回避にある。

## 7. データ保持方針

- `active`: 機能上必要な間だけ保持する。
- `completed`、`ended`、`deleted`: 現行どおり`terminal_at`から30日保持する。
- `failed`: 最終更新から90日を整理候補とし、実装前に削除・匿名化・通知要否を確定する。
- `draft`、`paused`: 最終更新から180日を整理候補とする。30日前通知と通知失敗時の扱いが完成するまで、自動削除を有効化しない。
- 実行履歴、通知履歴、操作履歴: 親Scheduleの現行保持・削除境界に従う。
- アプリケーションログ: 14日保持する。
- DBバックアップ: 日次7世代、最大14日保持する。

保存期間を過ぎたデータの削除または匿名化は自動化する。復元後のDBにも保存期限切れデータへのcleanupを適用する。バックアップから即時消去できない期間を利用者向け方針へ明記する。Discordへ投稿済みのメッセージはDB cleanupの対象外とする。

`failed`、`draft`、`paused`の新しい整理規則は未実装であり、要件、Migration、通知、cleanup、プライバシー、受入条件を確定するまで有効化しない。

## 8. 公開前限定テストと本番配置

一般公開直前に、少人数による短期間の限定テストを必ず実施する。実Discord、権限差、複数利用者の競合、長時間稼働、Bot再起動とRecovery、バックアップと復元可能性を確認する。

限定テスト合格後、無料の常時稼働環境を第一候補として本番配置する。無料枠で必要な稼働時間、DB容量、ログ、バックアップ、AI費用制御を満たせない場合だけ有料環境を検討する。これはインフラ配置の無料枠であり、利用者向け無料プランを意味しない。

## 9. 正式リリースのサブスクリプション方針

開発初期から製品構想に含めていたサブスクリプション契約を、正式リリース要件として導入する。ローカル開発・ポートフォリオ段階では課金せず、公開前限定テストでは決済Providerのsandbox／test modeだけを使用する。PAY.JPは過去からの候補であり、正式採用済みではない。

初期の第一案はguild単位契約とする。ただし、guild単位、利用者単位、複数guild契約、契約者、1 guild 1契約の最終決定は商品仕様策定時に行う。料金、プラン名、プラン数、無料枠、試用期間、AI利用枠、解約・日割り・返金、契約失効後の扱いも同じ監査で確定し、推測で追加しない。

基本予約機能とAI機能のEntitlementを分離し、AIの運営Budgetと顧客プランQuotaを別責務にする。決済障害、Webhook遅延、契約失効で予約や監査履歴を即時削除しない。利用者の文体学習、過去投稿学習、Embedding、利用者プロフィール生成は採用しない。

正式リリース後に必要となるAI API、常時稼働サーバー、PostgreSQL・ストレージ、バックアップ、ログ・監視、ネットワーク、決済手数料、税・返金・障害対応の予備費は、サブスクリプション収益で賄う。0円運用を正式リリース条件にせず、実用性と持続可能な原価管理を両立する。

正式リリースまでの順序は次のとおりとする。

1. サブスク初期仕様のGit履歴・旧文書監査（実施済み）
2. 料金・プラン・契約単位・無料枠の確定
3. Plan／Entitlement／Quota Domain
4. 決済Provider比較・選定
5. 決済Provider Port
6. Provider AdapterとWebhook
7. 契約開始・更新・解約・失効・猶予期間
8. プラン別機能・AI利用枠
9. 契約状態の利用者向け表示・管理操作
10. sandboxによる決済受入
11. 利用規約、プライバシーポリシー、特定商取引法表示の準備
12. 公開前限定テスト
13. 常時稼働環境構築
14. 正式リリース

現段階では2以降は未実装である。決済Providerの正式採用、契約単位・契約者、プラン名、月額・年額、無料プラン・試用、AI利用枠、予約件数・投稿回数・保存期間の差、解約・日割り・返金、支払い失敗時の猶予期間、失効後の新規予約・既存予約実行を商品仕様監査前に確定扱いしない。
## Phase 3第6項6C-1: リポジトリ運営基盤とCI

公開前監査の前提として、オープンソースライセンスを付与しないCopyright Notice、Security Policy、Contribution方針、bug／improvement Issue Form、GitHub Actions CIを整備する。独自コード・文書はall rights reservedとし、GitHub利用規約上の閲覧・fork以外の利用は明示的な書面許可を必要とする。CIはPython 3.14、Ruff、通常pytest、一時PostgreSQL統合pytest、Migration安全ラッパーを対象とし、Discord、OpenAI、決済、開発DB、productionへ接続しない。

構成追加とローカル検証に加え、2026-08-31にidentity書換え前の対応commitのGitHub Actions成功を確認した。2026-09-02には以前のidentity書換え後のdevelopについても利用者が成功条件を画面確認した。さらにGitHub username変更後の新tip `6a1f7c075f0b2dc238341879af59a2fda7d7ee7e`について、Success、test job成功、警告なし、Artifactsなし、badgeから`otolude/yoyaku-honpo`のworkflowへの遷移、作者OtoからGitHub profileへの遷移、main／developのtip一致を確認した。各runはその時点のcommitに対する証跡であり、過去runやローカル検証を新tipで再実行した扱いにしない。その後、利用者は旧username・旧履歴に紐づくrunを全件削除した。個別件数は未記録で、最終確認時のAll workflowsには新履歴4 runだけが残り、すべてSuccess、test job成功、Artifactsなし、警告なしだった。repositoryの匿名画面と標準Security導線も確認し、6Cの最終公開受入を完了した。

2026-09-02にidentity書換え後の83 commit／708 blobと現行・旧版PNG 8 blobを再監査し、Oto＋GitHub noreply以外の到達可能identity、旧個人名・旧Gmail、高確度secret候補、匿名化前画像・編集layer・生成前画像を検出しなかった。旧新tree、message、日時、parentとblob集合の一致を確認し、旧main／develop／MIT commitはbranch・tagから到達不能である。専用secret scanner未導入、GitHub内部保持、画像編集前データの不存在を保証できない限界を残し、6CのGit履歴・画像項目だけを完了とした。

同日のGitHub username変更対応では、その以前の記録と区別して88 commitを同一mappingで再構築した。author／committer name、message、parent、日時・timezoneを維持し、emailの新しいGitHub noreplyへの統一、usernameの置換、mapping済みcommit SHA参照の置換だけを行った。旧新reachable blobは各745件で、source、test、Migration、PNG、画像内容、著作権文書等の対象外byteは不変だった。通常公開ref上の旧identity metadata、旧username、旧SHA参照は0件だが、GitHub内部object、cache、過去run、既存cloneからの完全回収は保証しない。
