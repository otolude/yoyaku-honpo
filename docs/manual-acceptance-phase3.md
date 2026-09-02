# Phase 3 受入

Phase 3の受入をPhase 1・Phase 2から分離して記録する。第1段階、2A、2B-1、2B-2のProvider非依存基盤、2C-1のOpenAI Adapter隔離実装、2C-2の手動受入安全基盤、Phase 3第6項6Aのポートフォリオ掲載要件を対象とする。実AI Provider、APIキー、実費、決済、Plan、顧客Quotaは自動隔離受入に含めない。

- 実施日: 2026-08-30
- 実施者: Codex（自動テスト）
- 証跡: 重点テスト322件、通常pytest 815件成功／297件skip、専用PostgreSQL込み全pytest 1112件成功
- 2A証跡: 基盤重点テスト372件、Modal dispatch・ViewStore・競合重点テスト44件、残る認可境界6 node・17ケース、通常pytest 859件成功／324件skip、専用PostgreSQL込み全pytest 1183件成功。Migration upgrade／downgrade／upgrade、既存行backfill、downgrade guard、Alembic current／heads／check成功
- 2C-1証跡: OpenAI Adapter・設定・Worker・Bot lifecycle重点テスト128件、通常pytest 967件成功／349件skip、専用PostgreSQL込み全pytest 1316件成功。Alembic current／heads／check成功、既存6表＋AI Job／Budget 2表は終了時0件
- 2C-2証跡: 手動受入安全基盤重点テスト52件、2C-1回帰込み重点テスト180件、通常pytest 1019件成功／349件skip、専用PostgreSQL込み全pytest 1368件成功。Alembic current／heads／check成功、既存6表＋AI Job／Budget 2表は終了時0件
- 6C-1証跡: YAML／Issue Form／workflow静的検査、通常pytest 1019件成功／349件skip、専用PostgreSQL integration 349件成功、Ruff check／format、pip check、Migration current／heads／check成功、主要8表は終了時0件。identity書換え前の対応commitに対する2026-08-31のActions run #2（1分8秒、test job成功、Artifactsなし、Node.js 20警告なし）をOtoがGitHub画面で確認し、旧著作権方針版のrun #1を削除した
- 6C-3証跡: identity書換え前の対応commitをWSL2 Linux x86_64／Python 3.14.4／PostgreSQL 18.4隔離環境で検証し、通常pytest 1019 passed／349 skipped、PostgreSQL integration 349 passed、合計1368 passed、正式Migration、single head、主要8表の3時点0件、外向きIPv4／IPv6遮断、secret非露出、両containerの正常停止を確認した。同対応commitのGitHub Actionsは利用者が画面でworkflowとtest job成功、新しい警告なし、Artifactsなし、CI badgeの遷移を別途確認した。これらをidentity書換え後commitでの再実行結果として扱わない
- 6C履歴監査証跡: 以前のidentity書換えでは83 commit／708 blobのtree・blob内容不変を確認した。GitHub username変更時は別操作として88 commit／745 reachable blobを再構築し、email、username、mapping済みSHA参照だけを変更して対象外byteを維持した。新tip `6a1f7c075f0b2dc238341879af59a2fda7d7ee7e`で旧identity metadata、旧username、旧SHA参照、高確度secret候補は0件、PNG履歴8 blobは不変であり、利用者はActions成功、badge遷移、作者profile遷移を画面確認した
- 6C第三者notice証跡: `alembic/script.py.mako`をAlembic 1.19.1公式配布物のgeneric templateと照合し、公式LICENSEと配布物同梱LICENSEで一致した`Copyright 2009-2026 Michael Bayer.`およびMIT License全文を`THIRD_PARTY_NOTICES.md`へ収録した。これはAlembic由来templateのnotice不足だけを解消するもので、dependency全体、Discord UI、商標、repository visibility、public化を完了扱いにしない
- 6C repository改名証跡: 2026-09-02に利用者がGitHub repositoryをPrivateのまま`discord-ai-reminder-bot`から`yoyaku-honpo`へ改名し、originのfetch／push URLと現行公開用URL、CI badge、Security Policy導線を新名称へ整合した。ローカルdirectory、Python package／module、Compose、DB、環境変数、過去検証名は技術識別子または過去証跡として維持する。この改名だけでDiscord UI画像、「よやく本舗」の商標、visibility、public化、実Provider、ARM64、6C全体を完了扱いにしない
- 6C利用者判断証跡: 2026-09-02に利用者は、Discord UI画像4枚を現在の匿名化・最小crop・非提携表示でソース閲覧用portfolioへ掲載し、「よやく本舗」を採用予定名・商標確認未完了と明記して掲載する方針を承認した。少人数の無償closed test前は可能であれば商標専門家へ相談し、有償test、広告、契約、一般提供前は商標確認を必須の判断事項とする。Discordその他第三者による公式・公認・提携、画面掲載の無条件な適合、商標上の安全性・非侵害・登録可能性を示唆、表明または保証しない
- cdfa4de0 Actions証跡: 利用者がGitHub画面で`cdfa4de00fb5c6796c2250a890fcc1d4d8e54abe`のdevelop workflowを確認し、status Success、test job成功、Artifactsなし、警告なし、README badge正常表示、badgeから新しい`yoyaku-honpo` workflow画面への遷移、遷移先URLの旧repository名なしを確認した
- 集計: 確認済み 124件／未確認 3件（合計127件）
- Phase 3第1段階受入判定: 完了
- Phase 3第2段階2A受入判定: 完了
- Phase 3第2段階2B-1隔離受入判定: 完了
- Phase 3第2段階2B-2隔離受入判定: 完了（本番AI機能は利用不能）
- Phase 3第2段階2C-1隔離受入判定: 完了（実Provider受入は未実施）
- Phase 3第2段階2C-2安全基盤受入判定: 完了（live実Provider受入は未実施）
- Phase 3第6項6A隔離受入判定: 完了（12件／12件）
- Phase 3第6項6B-1受入判定: 完了（2件／2件）
- Phase 3第6項6B-2受入判定: 完了（1件／1件）
- Phase 3第6項6C-1隔離受入判定: 完了（6件／6件、GitHub Actions初回成功確認済み）
- Phase 3第6項6C受入判定: 未完了（3件／4件。repositoryはPublic、Private vulnerability reportingはEnabledだが、匿名Issue作成画面の確認と最終公開受入が残る）

## 自動テスト受入

- [x] `/post show`の空入力で`deleted`予約を候補へ返さない。
- [x] `deleted`と`削除済み`の状態検索で`deleted`予約を候補へ返さない。
- [x] canonical UUID完全一致とUUID prefix検索で`deleted`予約を候補へ返さない。
- [x] 種別、channel ID、channel名検索結果で`deleted`予約を候補へ返さない。
- [x] 所有者と管理者のどちらにも`deleted`予約を候補表示せず、他人・他guildの境界を維持する。
- [x] 非`deleted`予約、最大25件、`next_run_at ASC NULLS LAST, id ASC`の安定順を維持する。
- [x] canonical UUID直接入力では所有者と同一guild管理者が`deleted`詳細を参照でき、他人・他guildからは参照できない。
- [x] `/post list status:削除済み`と一覧から削除済み詳細を開く経路を維持し、status未指定一覧では`deleted`を除外する。
- [x] edit、delete、pause、resumeの既存Autocomplete状態・run・attempt境界を維持する。
- [x] 共通callbackの認可、Discord cache-only検索、失敗時の空候補と固定ログを維持する。
- [x] Autocomplete DTOと候補へ本文、内部DB ID、version、Discord message IDを追加しない。
- [x] Presenterの100文字上限、canonical UUID value、制御文字・mention安全化、channel ID fallbackを維持する。
- [x] DBモデル、Alembic Revision、削除済み予約・監査履歴・30日保持規則に変更がない。

## 実Discord受入

- [x] `/post show`の通常Autocomplete候補に`deleted`予約が表示されない。
- [x] canonical UUIDv7を直接入力した`/post show`では`deleted`詳細を参照できる。
- [x] `/post list status:deleted`から削除済み一覧・詳細を参照でき、通常一覧では`deleted`が表示されない。

## 実Discord証跡

- 実施日: 2026-08-30
- 実施者: Oto
- 対象環境: 実Discord
- 結果:
  - 通常候補では、空入力や検索時に削除済み予約が表示されないことを確認。
  - UUID直接入力では、正しい削除済み予約の詳細と「⚪ 削除済み」を確認。
  - `status=deleted`一覧、詳細表示、一覧へ戻る操作を確認。
  - 通常の`/post list`では削除済み予約が表示されないことを確認。
  - 別予約の誤表示がないことを確認。
  - Botターミナルに`ERROR`、`traceback`、`command_error`、`schedule_autocomplete_failed`、`internal_errors=1`以上がないことを確認。
- 証跡上の注意: 投稿本文、秘密情報、内部ID、他人の予約情報を記録しない。

## 第2段階2A 自動テスト受入

- [x] `display_name VARCHAR(32) NULL`と`display_name_source VARCHAR(8) NOT NULL DEFAULT 'unset'`、閉じたsource値、名前との整合CHECKを維持する。
- [x] Migrationで既存行を`NULL/unset`へ移行し、single head、upgrade、unsetだけのdowngrade、保存名がある場合のdowngrade拒否を維持する。
- [x] Domain validatorでtrim後1～32文字だけを許可し、空欄、改行、制御文字、Unicode category Cc／Cf／Csを拒否する。
- [x] 手動名の設定、変更、空欄解除、同値no-opを維持し、実変更だけversionと名前全文を含まないOperationLogを更新する。
- [x] draft、active、pausedの所有者と同guild管理者だけが編集でき、guild、認可、terminal、deleted、failed、expected version境界を維持する。
- [x] 本文変更と`clear_content`でmanual名を維持し、ai名だけを`NULL/unset`へ解除し、再生成Jobを作らない。
- [x] once、daily、weekly、日時不明の非AIフォールバックをJSTで決定的に生成し、DBへ保存しない。
- [x] 一覧・詳細・Autocomplete DTOへ名前表示に必要な値だけを追加し、ORM、内部DB ID、不要なversion、本文の情報境界を維持する。
- [x] 一覧の本文previewを除去し、詳細の正式本文を維持し、一覧・詳細・作成成功表示へ保存名またはフォールバックを表示する。
- [x] SelectとAutocompleteで予約名、状態、種別、日時、投稿先、短縮IDを100文字以内に表示し、本文を含めない。
- [x] 名前表示のMarkdown、mention、改行、制御文字を安全化し、長い名前・channel名でもDiscord表示上限を維持する。
- [x] 単一の名前Modalでsubmit、timeout、on_error時のinstance単位解除と、action lock・finished／closedによる二重submit防止を維持する。
- [x] 用途別固定prefixと非識別nonceを持つ4種類の外側Modalをdiscord.py 2.7.1の実ViewStoreへ複数同時登録でき、別端末、別詳細、別予約、別利用者のdispatchを相互に解除しない。
- [x] 古い名前Modalの遅延submitがBotへdispatchされ、DB処理前のdefer、expected version競合、固定案内、最新詳細更新へ到達する。
- [x] Modalのtimeout／on_errorは自分だけを解除し、Bot closeはregistry内の全Modalをsnapshotしてstop・wait回収する。

## 第2段階2A 実Discord受入

- [x] 所有者が共通詳細Viewの「🏷️ 予約名を編集」から手動名を設定・変更・解除でき、同値送信がno-opになり、本文変更・`clear_content`後もmanual名を維持する。
- [x] 同一guild管理者が他人予約の手動名を設定・変更・解除でき、versionと名前全文を含まないOperationLogへ管理者actorとsource遷移だけを記録する（PostgreSQL隔離受入済み）。
- [x] 名前編集は対象Scheduleだけを更新し、同一guildの別Scheduleと別guildのSchedule、そのversion、名前、source、updated_at、OperationLogを変更しない（PostgreSQL隔離受入済み）。
- [x] 名前Modal submit時に現在の利用者、guild、許可ロール、管理者権限を再検証し、別利用者、DM、設定外guild、許可ロール喪失、管理者権限喪失を安全に拒否する（Fake Interaction・実ViewStore隔離受入済み）。
- [x] 認可・guild・状態・version拒否ではDB、version、updated_at、OperationLog、別予約を変更せず、取得不能時は旧Viewを解除して`AllowedMentions.none()`付きの固定案内だけを返し、本文、予約名、UUID、内部version、例外情報を露出しない（PostgreSQL・Fake Interaction・実ViewStore隔離受入済み）。
- [x] 競合時に固定案内と最新詳細が表示され、別予約や古い名前を表示しない。
- [x] 一覧、詳細、Select、Autocomplete、作成成功表示に保存名または正しいJSTフォールバックが表示される。
- [x] 一覧、Select、Autocompleteに本文の一部が表示されず、詳細の正式な本文欄は維持される。
- [x] Modalの閉じる、timeout、再オープン、二重操作とBot終了時に異常応答や固定イベント以外のERRORが発生しない。

## 第2段階2A 実Discord証跡

- 実施日: 2026-08-30
- 実施者: Oto
- 対象環境: 実Discord
- 確認済み:
  - 一覧、詳細、Select、Autocomplete、作成成功表示と既存予約の詳細で、保存済み予約名またはJSTの非AIフォールバックを確認した。単発は`単発予約 M/D HH:MM`、毎日は`毎日予約 HH:MM`、毎週は`毎週予約 曜日 HH:MM`の形式で、既存の状態、種別、日時、投稿先、短縮IDも維持されていた。
  - 一覧、Select、Autocompleteに本文previewが表示されず、詳細の正式な本文欄には対象予約の正しい本文全文が表示された。別予約の本文は混在しなかった。
  - 修正後は同じ予約の詳細画面A・Bで予約名Modalを同時保持でき、Bが「競合修正後の最新名」を保存した後、Aの古いModalもBotへdispatchされた。Discordの汎用エラーは発生せず、固定競合案内とBの最新名を持つ最新詳細が表示された。Aの古い名前は保存されず、別予約も変更されず、新しい`/post show`でも「競合修正後の最新名」を確認した。
  - 予約名Modalを×で閉じても名前は変化せず、同じ詳細から再オープンできた。二重操作は1回だけ反映され、16分後の期限切れModalではDB更新がなく、親詳細から新しいModalを開けた。Modalを開いた状態で`Ctrl+C`により正常終了し、Task未回収警告はなく、再起動後も保存済み名を維持して、新しい`/post show`から新しいModalを開けた。
  - 上記確認中、Botログに`ERROR`、`traceback`、`internal_errors=1`以上はなかった。
- 実Discord確認と隔離受入の対応:
  - 手動名は、所有者による設定、変更、同値no-op、空欄解除と、本文変更・`clear_content`後のmanual名維持を実Discordで確認した。同一guild管理者による他人予約の設定・変更・解除と監査境界はPostgreSQL隔離テストで直接確認した。
  - 状態境界は、draft、active、pausedで編集可能、completed、ended、deleted、failedで編集不可を実Discordで確認した。別利用者、DM、設定外guild、詳細表示後の許可ロール喪失・管理者権限喪失、古いModal submit時の現在認可再検証、拒否時のDB不変性と固定情報境界はPostgreSQL、Fake Interaction、discord.py実ViewStoreの隔離テストで直接確認した。

### 複数Modal競合の修正前後

- 修正前の実Discord確認では、Bの最新名が保存されAの古い名前と別予約が変更されないデータ保護結果を確認したが、古いAはBotへdispatchされずDiscordの汎用エラーになった。この結果をCAS競合受入の成功とは扱わない。
- 外側Modalのnonce化後に改めて再試験し、古いAがBotへdispatchされ、固定競合案内と最新詳細が表示されることを確認した。この修正後の再試験だけをCAS競合の実Discord成功証跡とする。

## 第2段階2B-1 自動隔離受入

- [x] Job status、固定result code、Budget period、状態遷移、30日／90日保持判定を閉じたDomain値として検証する。
- [x] `BudgetPolicy`は設定由来の正のJPY microunits・日次／月次回数を検証し、bool、0、負数、overflow、不正通貨、月次未満の日次を拒否する。
- [x] JSTの日次・月次境界を検証し、50／500／100円相当をDB CHECKや変更不能な上限定数へ固定しない。
- [x] `NameGenerator` Portと外部通信しないDisabled Generatorを用意し、Fake Generatorを本番設定から選択できない。
- [x] JobはScheduleへの`ON DELETE RESTRICT`、Schedule version一意、lifecycle CHECK、pending／lease／terminal index、processing全体1件の部分一意indexを持つ。
- [x] Budget bucketは日次／月次、月初、非負、versionだけをDBで検証し、Schedule、guild、利用者、契約情報を持たない。
- [x] AI有効・Generator利用可能・本文あり・source unsetの作成と本文実変更だけで同一transactionへJobを登録し、重複を正常に抑止する。
- [x] AI無効、Disabled Generator、manual名、clear content、本文以外の編集、no-opではJobを作らず、既存unset予約をbackfillしない。
- [x] Job登録をsavepointで隔離し、固定イベント以外へ本文、名前、UUID、Discord ID、秘密情報を出さず、DB接続全体の障害は隠さない。
- [x] 保守的CAS保存はSchedule version、source、本文を再検証し、AI名保存時にSchedule version／updated_atを増やさず、名前全文なしのsystem OperationLogを残す。
- [x] lease切れprocessingを再試行・Budget返却せず`abandoned/startup_abandoned`へ移し、pendingを維持するRecovery基盤と保持期限cleanup基盤を維持する。
- [x] Migrationは2テーブル、制約、index、FKを追加し、backfillせず、データ存在時downgradeを拒否してsingle headを維持する。

## 第2段階2B-2 自動隔離受入

- [x] pendingを`created_at, id`順に選択し、Schedule→Jobをlockしてversion、source、名前NULL、本文、draft／active／pausedを再検証する。
- [x] AI無効、Generator unavailable、価格不明・不正、stale、manual、本文なし、terminalをGenerator呼び出し前に固定result codeで安全に終端化する。
- [x] JST daily→monthlyの固定lock順で日次回数、月次回数、月次最大費用を同一transactionへ悲観予約し、50／51、500／501、費用ちょうど／超過、設定変更を検証する。
- [x] 複数claimと部分一意indexでも全processのprocessing最大1を維持し、Budget DB失敗時はtransaction rollbackでpendingを再評価可能にする。
- [x] claim commitとSession close後だけ、本文と非識別制約だけの不変DTOをFake Generatorへ渡し、Session、Repository、ORM、IDを保持しない。
- [x] Fake Generatorの成功、timeout、cancel、typed error、invalid responseを自動再試行なしで固定分類し、1 cycle最大1回、process内Semaphore 1を維持する。
- [x] Generator待機中も別transactionがScheduleをlockでき、不要なDB connection、transaction、row lockを保持しない。
- [x] finalizeは新transactionでSchedule→Jobをlockし、CAS成功時だけAI名を保存してSchedule version／updated_atを維持する。
- [x] manual化、version変更、本文解除、terminal化、Recovery先行では生成結果を破棄し、別予約、Run、Budgetを変更しない。
- [x] system OperationLog、Job、Budget、通常ログへ本文、生成名、応答、UUID、内部ID、Discord ID、guild、契約情報、例外全文を保存しない。
- [x] startup schema確認後にName Generation Recoveryを実行し、lease切れをBudget返却・再試行なしで`abandoned/startup_abandoned`へ移し、pendingを維持する。
- [x] Recovery完了後、AI有効かつGenerator availableの場合だけ5秒間隔のpollを開始し、cycle例外後もloopを維持する。
- [x] shutdownは新規poll停止、Generator cancel・await、`abandoned/shutdown_unknown`、既存View／Worker、Engine disposeの順を維持し、二重closeとTask回収を安全に扱う。
- [x] 既存cleanup loopへJob30日・bucket90日を個別rollback可能なtransactionで接続し、pending／processingとSchedule blockerを維持する。

本番DIは外部通信しない`DisabledNameGenerator`だけで、`AI_NAME_GENERATION_ENABLED=false`が初期値である。2B-2完了時点でも外部AI機能は利用できず、実Discord受入は行わない。

## 第2段階2C-1 自動隔離受入

- [x] OpenAI公式SDK 2.54系を正式`.venv`へ解決し、実SDK＋Mock transportでResponses引数、構造化出力、retry 0、timeout、HTTP request、typed error、cancel、closeを無通信検証する。SDKを`infrastructure` Adapterへ隔離し、Domain、Application、Workerから直接参照しない。
- [x] 通常候補`gpt-5.6-luna`と品質比較候補`gpt-5.4-nano-2026-03-17`だけを許可し、Deprecatedの`gpt-5-nano`、固定されないnano alias、空・未知モデルをfail-closedで拒否する。
- [x] Lunaの日付付きsnapshotを推測せずalias監査を要求し、モデル別の公式単価、reasoning effort、入力・出力上限が一致しない設定を拒否する。
- [x] stateless Responses APIへ本文と固定条件だけを渡し、`store=false`、構造化出力、tools・search・background・conversation未使用と、ID・日時・投稿先・契約情報の非送信を検証する。
- [x] 既存2,000文字、UTF-8 byte、保守的token、出力tokenの多層上限を適用し、超過時はProviderを呼ばない。
- [x] JSON型、message／候補数、空、複数行、33文字以上、Cc／Cf／Csを拒否し、最後に既存`GeneratedScheduleName` validatorを通す。
- [x] SDK retry 0、接続・request timeout、cancel、429、5xx、接続、認証、モデル不正を文字列照合なしのtyped分類で処理し、1 Jobから再呼出ししない。
- [x] 公式単価、JPY microunits為替、安全係数、最大tokenから悲観最大費用を切上げ計算し、0、負数、overflow、古い価格、不整合を拒否する。
- [x] APIキー、本文、生成名、応答、UUID、Discord ID、Provider request ID、実usage、例外全文をJob、Budget、OperationLog、通常ログへ保存しない。
- [x] Provider／AI初期無効とAPIキー未設定を維持し、有効フラグまたは一部設定だけではJob、poll、SDK import、外部通信を開始しない。
- [x] Generator timeout、usage欠落、cancel、失敗でもBudgetを返却せず、Session、transaction、row lockを閉じた既存Worker境界とshutdown・二重close回収を維持する。
- [x] 運営設定の単一モデルだけを扱い、将来のEntitlement、顧客Quota、ModelSelectionPolicy、Plan、契約、決済を実装せず、Job／Budget schemaとMigrationを変更しない。

GPT-5.6 LunaとGPT-5.4 nanoはいずれも正式採用前であり、実Provider受入は未実施である。APIキー未設定、Provider無効、外部通信なし、費用発生なしを維持する。OpenAI側の標準abuse monitoring保持最大30日は2C-1時点で許容し、ZDRと国内処理は正式公開前に再評価する。Providerの学習不使用と、Bot自身が学習・Embedding・プロフィール生成を行わない方針は別々に維持する。

## 第2段階2C-2 手動受入安全基盤の自動隔離受入

- [x] 独立CLIの引数なし、help、dry-runではAPIキーを読まず、SDK client、DNS、HTTPを開始せず、安全な固定計画だけを表示する。
- [x] liveはProvider、専用target、明示モデル、最大request数、悲観最大費用、live操作を束縛した完全一致confirmationが揃わなければclient構築前に拒否する。
- [x] 専用APIキーをprocess環境からだけ読み、`.env`、通常Settings、CLI引数、subprocess、ファイル、DBへfallbackまたは保存しない。
- [x] 公式OpenAI endpointだけを完全一致で許可し、HTTP、別host／port、userinfo、query、fragment、任意base URL、環境proxy、redirect追従を拒否する。
- [x] 単発、毎日、毎週、絵文字、Markdown／mention風、prompt injection風の固定6合成caseだけを使い、任意ファイル、stdin、DB、実利用者本文を入力できない。
- [x] Luna aliasとGPT-5.4 nano固定snapshotだけを明示選択でき、Deprecated、未固定alias、未知model、重複を拒否し、両modelへ同じcase・instruction・Schema・token上限を渡す。
- [x] 監査済み単価、JPY microunits為替、安全係数、token上限からmodel別・全体の悲観費用を切上げ、request数と計算値に一致しない上限を拒否する。
- [x] request直前にprocess内回数・悲観費用を消費し、timeout、cancel、失敗、結果不明でも返却せず、固定case数×model数を超過できない。
- [x] 各requestを直列に1回だけ実行し、SDK retry、自動追加呼出し、model fallback、Batch、並列、再開を行わず、最初の障害またはcancelで残りを停止する。
- [x] client close、二重close、cancel時Task回収を検証し、通常Bot composition、Worker、通常pytest、DB Job／Budgetからlive runnerへ接続しない。
- [x] live成功時だけ合成case ID、model、検証済み生成名、文字数、経過時間、回数、悲観費用を対話出力でき、dry-run、固定エラー、終了summary、通常loggerへ生成名を複製しない。
- [x] APIキー、Authorization、合成本文全文、raw request／response、request ID、raw usage、例外全文を出力・ログ・永続層へ露出せず、Mock transport以外の実API通信がないことを確認する。

## 2C 実Provider受入

- [ ] 利用者の明示許可後、専用Project、制限付きキー、予算・アラートを確認し、同じ合成caseでLunaとGPT-5.4 nano固定snapshotの日本語品質、32文字、応答時間、実token量、費用、保持、請求、dashboard設定を実APIで確認する。

この受入は中止せず公開前へ延期する。専用Projectは作成済みだが、内部識別情報は記録しない。Projectでは`gpt-5.6-luna`と`gpt-5.4-nano`だけを許可し、各モデル60,000 TPM・10 RPMとしている。現在は残高0 USD、支払い方法未登録、APIキー未作成、API通信0回、費用発生なしであり、Project作成を正式Provider採用の証跡にはしない。

課金可能な状態を作る前に利用者の明示許可を再取得する。最低プリペイド購入が必要な場合はAuto-rechargeを無効にし、その購入額を実試験のAPI原価と混同しない。固定匿名6 caseをLunaへ6回、別runでGPT-5.4 nano固定snapshotへ6回送信し、両runとも各requestの間隔を60秒以上空ける。retry、fallback、Batch、並列実行、自動保存は行わない。悲観費用はLuna 333,600 JPY microunits／回、GPT-5.4 nano 334,200 JPY microunits／回、合計4,006,800 JPY microunits（約4.0068円）である。試験後にモデルの使い分けを決め、プラン別モデル・回数・機能は商品仕様策定時に確定する。この延期記録によって、実ProviderとARM64 Linux実機の既存2項目のチェック状態は変更しない。

## 2C 公開前環境受入（2C-1完了条件外）

- [x] Python 3.14.4／WSL2 Linux x86_64で公式`openai` packageの依存解決、import、実SDK＋Mock transport contractを確認し、package metadata上の対応と実機確認を区別する。
- [ ] 正式な常時稼働候補と同等のARM64 Linux実機で、公式SDKと必須native wheelの依存解決、import、Mock transport contract、shutdownを確認する。

ARM64実機確認は2C-1のAdapter隔離完了条件には含めず、配置architecture確定後の公開前環境受入とする。package metadataやx86_64での成功だけをARM64確認済みとは扱わない。実Provider品質、請求、保持、Project dashboard設定も別の明示受入であり、上記自動隔離受入へ含めない。

## Migration接続先安全性 自動隔離受入

- [x] Migration targetをtest／development／productionの閉じた値とし、未設定、空、空白、大文字違い、bool風、不明値をfail-closedで拒否する。
- [x] testは`discord_bot_test`と実行プロセスの`TEST_DATABASE_URL`だけ、developmentは`discord_bot_dev`と既存開発設定だけ、productionは明示DB名と実行プロセスの`DATABASE_URL`だけを使用し、環境間fallbackを行わない。
- [x] Pythonラッパーはheads／history、current／check、upgrade／downgrade／stamp、autogenerateを分類し、書込・生成操作では`target:database:operation`の完全一致確認を接続前に要求する。
- [x] Alembic CLI直接実行、未知command、判定不能なprogrammatic invocation、offline modeを`alembic/env.py`で拒否し、`TEST_DATABASE_URL`が存在するだけでは許可しない。
- [x] URL上のDB名を早期確認し、接続後の`SELECT current_database()`完全一致と読取transaction終了後だけMigration contextを開始する。
- [x] URL、password、user、host、portをラッパーの通常出力・例外へ露出せず、秘密URLをcommand引数やsubprocessへ渡さない。
- [x] 専用PostgreSQLでcurrent／check／upgradeと実DB名不一致のDDL前拒否を検証し、拒否前後のRevision、schema、主要8表件数を不変に保つ。
- [x] Bot起動時schema verification、pytest test DB fixture、既存Revision固有downgrade guardを維持し、DBモデルとMigration Revisionを変更しない。

## Phase 3第6項6A 要件・掲載境界・匿名化基準・成果物範囲

以下は[ポートフォリオ掲載計画](portfolio-plan.md)と関連文書の差分だけで全条件を直接確認した隔離受入である。pytest nodeは文書要件を検証しないため証跡に使用せず、identity書換え後の基準commit `bd2c9be91e8ddeb936a4b37888099d649b177375`からの文書差分、Git状態、Markdown・リンク・秘密情報検査を証跡とする。これはtree内容上の文書基準であり、過去の実行証跡を新commitで再実行したという意味ではない。成果物が基準へ準拠したことは6B／6Cへ分離する。

- [x] 目的と、採用担当者、技術者、運用・セキュリティ確認者、将来の協力者という対象読者、および日本語本文＋短いEnglish summaryの言語方針を定義する。
- [x] READMEを短い入口、`docs/portfolio/`を第三者向け要約、既存要件・設計・運用・受入文書を正本とする役割分担を定義する。
- [x] 掲載範囲と非掲載範囲を定義し、実装済み、自動隔離テスト済み、実Discord確認済み、設計のみ、延期中、未実装、将来計画を区別する表記規則を定義する。
- [x] 6BのREADME、architecture、feature flows、security and privacy、verification、screenshot policy、asset manifest、匿名化画像という成果物範囲を定義する。
- [x] 実利用者データと実IDを使わず、専用開発guildの新規合成データで撮影する方針を定義する。
- [x] 復元不能な焼き込み匿名化、metadata除去、元画像・編集レイヤー・途中画像の非追跡という基準を定義する。
- [x] asset manifestへ記録する情報と、実guild名・内部ID・秘密等の記録禁止情報を定義する。
- [x] 構成図へ実ID・接続情報を載せず、現在接続、初期無効、未実装の将来機能を線種とラベルで区別する基準を定義する。
- [x] READMEの最短セットアップ入口とoperationsの詳細手順を分離し、開発DB・test DB・Migration安全ラッパー・破壊的操作非掲載の基準を定義する。
- [x] OpenAI初期disabledを維持し、通常READMEへ実Provider live confirmation、APIキー、課金手順を掲載しない基準を定義する。
- [x] テスト・受入証跡へ時点、commit、環境、証跡区分、限界、固定件数が古くなる可能性を記録し、件数だけで品質を保証しない基準を定義する。
- [x] 6A／6B／6Cの完了条件と6C監査一覧を定義し、実Provider受入とARM64 Linux実機確認の既存未確認2項目を完了扱いにしない。

## Phase 3第6項6B-1 README・構成図・機能フロー

文書成果物そのもの、コード・Migration・受入記録との主張照合、ローカルlink・Mermaid・Markdown・秘密情報検査を直接証跡とする。pytestを文書成果物の代替証跡にしない。

- [x] READMEを短い入口へ再構成し、`docs/portfolio/`のarchitecture、feature flows、security and privacy、verification、screenshot policy、asset manifest、Mermaid図、実装状態マトリクス、安全なセットアップ案内を作成する。実装済み・各テスト区分・実Discord確認済み・基盤のみ・未確認・未実装・将来計画を分け、実Provider、ARM64、常時稼働、一般公開、契約・決済を実績にしない。
- [x] README冒頭の日本語一行サマリと短いEnglish summaryについて、2026-08-30にOtoが最終文面を承認し、READMEへ完全一致で反映する。未公開、AI初期disabled、実Provider未確認を明記し、承認待ち表示を残さない。

## Phase 3第6項6B-2 匿名化画面資料

2026-08-30にOtoが専用開発guildの撮影用private channelと新規合成データだけで4画像を作成した。repository上のPNG構造・hash・文字列・目視監査、Markdown link・alt text・manifest対応を直接証跡とし、6Aの方針やchecklist形式だけを実asset完成の代替証跡にしない。Discord商標、Git履歴、公開状態の最終監査は6Cへ残す。

- [x] 専用開発guildの新規匿名合成データだけで一覧・詳細・編集Modal・投稿結果を作成し、READMEとfeature flowsの説明的alt text、manifestの用途・掲載先・不透明焼き込み・metadata除去・確認日・確認者・SHA-256へ対応付ける。既存実Discord受入画像、実利用者データ、元画像、編集レイヤーを含めず、完全UUID、実ID、秘密・接続情報を露出しない。

## Phase 3第6項6C 再現確認・秘密情報・ライセンス・公開状態の最終監査

以下は6B成果物完成後の最終監査と利用者判断を記録する。第四項だけが未確認である。

Alembic由来templateのMIT notice不足は`THIRD_PARTY_NOTICES.md`で解消した。現在wheel、sdist、container、実行ファイルは配布せず、将来の配布物に含まれるdependencyの全面的なnotice監査は配布前事項として残す。第三項はソース閲覧用portfolioの範囲に限定した監査と2026-09-02の利用者判断により確認済みとする。

GitHub repositoryはPrivateのまま`yoyaku-honpo`へ改名した後、Public化された。公開用URLとCI／Security導線は新名称へ更新する一方、技術識別子と過去証跡の旧名称は維持する。監査で指摘された公開前blockerであるAlembic由来templateのMIT notice不足と、Discord markを含むGitHub repository名／URLは解消済みである。Private vulnerability reportingはEnabledで、Issue作成画面はGitHub標準の`Report a vulnerability`導線へ一本化する。匿名状態でのIssue作成画面確認と最終公開受入が残るため、以下の第四項は未確認のままとする。

- [x] READMEと全詳細文書のリンク、隔離環境での再現手順、実装主張とコード・Migration・受入証跡の対応を確認する。identity書換え前の対応commitのGit管理外clean archive、Docker internal network、namespace共有runner、tmpfs専用DBによる保存証跡で、追跡176ファイル、source・依存・wrapper、通信遮断、正式Migration、通常／integration pytest、主要8表0件、secret非露出、正常停止を直接確認した。書換え時に対応する旧新tree OID一致を確認したが、新commitで再実行した結果とは扱わない。
- [x] Git追跡ファイルとGit履歴の秘密情報、および全画像のmetadata・写り込み・復元可能性・元画像／編集レイヤー非追跡を確認する。以前のidentity書換え後の83 commit／708 blobではtree・blob内容不変を確認した。GitHub username変更時は別の88 commit／745 blob再構築としてemail、username、mapping済みSHA参照だけを変更し、source、test、Migration、PNG、画像内容、著作権文書等の対象外byteを維持した。新tip `6a1f7c075f0b2dc238341879af59a2fda7d7ee7e`の通常公開refで旧identity metadata、旧username、旧完全・短縮SHA参照、高確度secret候補は0件だった。現行4枚＋旧版4枚のPNGは従来SHAと一致し、既存の構造・画素・目視監査結果を維持する。GitHub内部object、cache、過去run、既存cloneからの完全回収は保証しない。
- [x] 採用LICENSE・著作権表記、dependency license、第三者素材、Discord／OpenAIの商標・画面掲載条件を確認し、2026-09-02の利用者判断を記録する。独自部分はAll rights reserved、Alembic由来templateは個別MIT notice、dependency packageは非vendor・非配布として区別した。Discord UI画像4枚は現在の匿名化・最小crop・非提携表示を維持してソース閲覧用portfolioへ掲載し、「よやく本舗」は採用予定名・商標確認未完了と明記して掲載する。少人数の無償closed test前は可能であれば商標専門家へ相談し、有償test、広告、契約、一般提供前は商標確認を必須の判断事項とする。公式・公認・提携、画面掲載の無条件な適合、商標上の安全性・非侵害・登録可能性は表明または保証しない。wheel、sdist、container、実行ファイルの配布前監査は将来事項として残す。
- [ ] repositoryがPublic、Private vulnerability reportingがEnabledであることを前提に、匿名状態のIssue作成画面でbug／improvement、maintainer限定blank Issue、GitHub標準`Report a vulnerability`、Security Policy、CI・badgeを確認し、Phase 3の最終公開受入を判定する。

## Phase 3第6項6C-1 リポジトリ運営基盤とCI 自動隔離受入

以下はローカルのファイル内容、YAML schema、無通信テスト、専用test DBと、利用者がGitHub画面で確認したActions runを直接証跡とする。CI成功だけで6C全体を完了扱いにしない。repositoryはPublic、Private vulnerability reportingはEnabledだが、匿名Issue作成画面の確認と最終公開受入は上記6Cの未確認条件へ残す。ソース閲覧用portfolioのdependency license、第三者素材・商標・画面掲載条件と、Git履歴・画像監査は2026-09-02に完了したが、将来の配布前監査とは区別する。

2026-09-01に利用者がidentity書換え前の対応commitについて、workflow実行成功、test job成功、新しい警告なし、Artifactsなし、READMEのCI badgeからworkflow画面へ移動可能であることをGitHub画面で確認した。run番号と所要時間は提供されていないため推測せず、ローカルのPostgreSQL隔離検証とは別の過去の画面確認証跡として扱う。2026-09-02には新develop `0d3b0a5956b61a7a1cdd30126f5ad3d3caf163b1`について同じ成功条件とcommitのGitHub accountへの関連付けを利用者が画面確認した。これは新履歴のCI証跡であり、過去のローカル隔離検証を新commitで再実行したことを意味しない。

GitHub username変更後の新tip `6a1f7c075f0b2dc238341879af59a2fda7d7ee7e`についても、利用者がdevelopのSuccess、test job成功、警告なし、Artifactsなし、README badgeの正常表示と`otolude/yoyaku-honpo` workflowへの遷移、作者OtoからGitHub profileへの遷移、main／developのtip一致を画面確認した。このrunだけを今回の新tipに対するCI証跡とし、identity書換え前のtest・build・DB検証を新SHAで再実行したとは扱わない。その後、旧username・旧履歴に紐づくActions runを全件削除した。個別件数は未記録で、All workflowsに残る新履歴の`6a1f7c075f0b2dc238341879af59a2fda7d7ee7e`と`1943b819a3629d9c69267bec240d916174f7249d`の2 runは、両方ともStatus Success、test job成功、Artifactsなし、警告なし、旧username表示なしである。

- [x] オープンソースライセンスを付与せず、`Copyright (c) 2026 Oto. All rights reserved.`、公開目的、書面による個別許諾、GitHub上の閲覧・fork、第三者素材の除外、無保証をCopyright Noticeへ明記する。
- [x] SECURITYで公開Issueへの脆弱性投稿を禁止し、有効化済みのPrivate vulnerability reportingとGitHub標準の`Report a vulnerability`導線へ一本化する。秘密非添付、対応期限非保証、個人連絡先なしを維持する。
- [x] CONTRIBUTINGとbug／improvement Issue Formを追加し、Issueは受け付ける一方で外部PRを積極募集せず、blank Issue、Code of Conduct、PR templateを追加しない。
- [x] CIをdevelop push／Pull Request、`contents: read`、concurrency cancel、Python 3.14、pip check、Ruff、通常pytest、専用PostgreSQL integrationへ限定する。
- [x] CIのMigrationを安全ラッパー経由のtest targetと実DB名照合だけにし、開発・production DB、実`.env`、Discord、OpenAI、決済、live acceptance、deploy、releaseへ接続しない。
- [x] YAML・Issue Form・workflow境界、通常／PostgreSQL pytest、Ruff、Migration current／heads／check、秘密情報、既存画像hashをローカルで検証し、GitHub上の成功やARM64確認の代替にしない。

## Phase 3第6項6C-2 DB非依存隔離検証

- [x] identity書換え前の対応commitの追跡176ファイルをGit blob hash一致で展開し、新規venv、`env -i`、公開PyPI、テスト時外向きsocket遮断のLinux x86_64／Python 3.14.4環境で、依存install、build isolationによるsdistとsdist由来wheel、pip check、Ruff、DB非依存pytest 1,019件を完了した保存証跡を監査する。通常pytestの349 skipを`TEST_DATABASE_URL`未設定によるPostgreSQL統合testとして区別し、artifact内容、build依存、著作権・license metadata不足、filesystem namespace非分離、build用venv削除、lock file不在の限界を記録する。

本項目は保存済み証跡のmanifest・inventory・`final-summary`・`build-dependencies`・`artifact-audit`等が相互に一致したことだけを直接証跡とする。このidentity書換え前commitの証跡単独ではDB統合確認、ARM64、実Provider、同commitのGitHub Actions、法的配布可否は未確認である。完全な旧新対応はGit管理外の非公開mappingで保持し、書換え時のtree一致を確認したが、新commitでの再実行結果とは扱わない。後続のDB統合とGitHub画面確認は次の6C-3へ分離する。

## Phase 3第6項6C-3 PostgreSQL internal network隔離再現

identity書換え前の対応commitのGit管理外clean archiveを、WSL2 Linux x86_64、Python 3.14.4、pip 25.1.1、pytest 9.1.1、`postgres:18.4-bookworm`で検証した。追跡176ファイルはGit blob不一致0件で、runner内source、44行の依存一覧、wrapper hashが一致し、pip checkが成功した。runnerは非root、read-only、全capability drop、no-new-privilegesでDocker socketを持たず、postgres_testのnetwork namespaceをcontainer IDとinodeで共有した。PostgreSQLはinternal networkだけに接続し、公開port・named Volumeなし、DBはtmpfsである。この実行をidentity書換え後commitでの再実行結果とは扱わない。

`127.0.0.1:5432`の専用DBだけへ接続でき、公開IPv4／IPv6への直接接続は`ENETUNREACH`、gatewayの既知portとloopback 55432は接続拒否だった。healthと`SELECT 1`、正式Migrationラッパーの`upgrade`／`current`／`heads`／`check`、`a41f8c7d2e90`のsingle headを確認した。主要8表はMigration直後、integration後、停止直前の3時点で全て0件だった。通常pytestは1,019 passed／349 skipped、integrationは349 passed／0 skipped／0 errors、合計1,368 passedでwarningなし、両ログの合成secret完全一致0件、両containerは`Exited (0)`だった。

Git管理外の永続証跡は通常ファイル229件、rootを含むdirectory 24件、symlink 0件、rootを含む全entry 253件、evidence 38ファイルで、通常ファイルmanifest `24662c3587e3050e3c28207bab9c4b0958c316ee38ed686f683cc2f5a631a685`、正規化inventory `029255b2d26034b571b5c9542ba55318667c162f6ec113e0777eeb7b75620456`、evidence manifest `ce587756d73a18c7103285ace38b861887b668c4be797b1304292ae08154762f`を再照合した。証跡は公開用手順や配布物ではなく、0600の専用secretファイルに実credentialではない合成値が残るためGitへ追加しない。

build段階はPyPIへ接続し、runnerとDBはnetwork namespaceを共有した。Docker Desktop daemon、WSL2 kernel、管理者侵害への耐性、停止後に失われたtmpfs DBの現物、lock fileなしでの将来依存再現性は保証しない。ARM64、実Provider、法的配布可否、最終公開判断は未確認のままとする。
