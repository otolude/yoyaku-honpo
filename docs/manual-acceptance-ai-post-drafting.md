# Phase 4 AI投稿本文下書き受入

AI投稿本文下書きをPhase 3から分離して管理する。Phase 3の確認済み125件／未確認2件（合計127件）および第6項6Cの4件／4件は変更しない。本書のチェックはPhase 4の証跡だけで更新し、文書化、実装、自動隔離テスト、PostgreSQL統合、実Provider、実Discord、ARM64 Linux実機を混同しない。

## 現在の判定

- Phase 4A 文書化: 完了
- Provider非依存Domain型とvalidation、one-shot Application Service、Usage Repository Port、Budget／rate limit／receipt Domain: 実装・自動隔離テスト済み
- 本文専用ORM schema: `post_draft_operator_budget_buckets`、`post_draft_rate_limit_buckets`、`post_draft_usage_reservation_receipts`の3 tableとrevision `c72e91f4b6a3`を実装・実DB検証済み
- PostgreSQL Usage Repository、Usage reservation orchestration、Usage cleanup、独立Usage Settings、無効Composition、production未接続のOpenAI Responses API Adapter、UI Session／Controller、Discord UI部品、`PostDraftRuntime`: 実装・自動隔離テスト済み
- Phase 4I: Post Draft Accept後も既存のaccepted terminal contractを維持し、独立したSchedule Sessionへ引き渡す。単発／毎日／毎週の選択・入力・編集・最終確認、認可、競合抑止、冪等な予約作成境界まで実装・自動テスト・PostgreSQL統合済み。採用済みの作業区分「Stage C」ではType Cancelと単発／毎日／毎週の作成・初回配信・recurrence lifecycleまで確認済みであり、その後の残存非AI受入ではstale ModalとEdit／Confirm／Cancel競合、2,000文字境界、mention安全性とMarkdown／URL表示、予約種別選択ViewのOption A timeout、Bot本人のSend Messages権限喪失Option A、Bot再起動／recovery Option Aを確認済み。Stage Cは正式計画上の独立Stage名ではなく、Phase 4I内の受入範囲を追跡する呼称である
- `/post compose`: 既存guild限定`/post` Groupへ登録し、開発・検証専用Application／Guildでguild限定command、AI無効表示、初期Mode／PreviewのCancel、ManualのPreview／Edit／Accept、確認表示の限定したescape境界を実Discord確認済み。global command syncは行っていない
- production Composition: Provider gateはfalseで`DisabledPostDraftGenerator`を使用し、Provider Settings loader、`AsyncOpenAI`、OpenAI Adapterは未接続。AI buttonはdisabledの「AIで作成（準備中）」表示
- Manualフロー: Preview／Edit／Acceptと、Accept後の予約種別選択、Type Cancel、単発／毎日／毎週の条件入力・最終確認・予約作成・初回配信に加え、stale Modal競合、2,000文字本文、mention／Markdown／URL境界、予約種別選択ViewのOption A timeout、Bot本人のSend Messages権限喪失Option Aを開発・検証専用の実Discordで確認済み。Previewの`Embed.description`だけを表示用に変換し、Domain、Session、Edit Modal初期値、Accept本文はrawのまま保持する。「予約を確定」までは予約保存・投稿を行わない
- Usage cleanupのruntime wiring／定期実行、Plan／Entitlementとプラン別利用枠: 未実装
- 正式model、価格・費用承認、Option A以外のUI timeout仕様: 未決定
- 自動隔離テストとPostgreSQL統合テスト: read-only DB audit supportのfake boundary validationと実PostgreSQL integrationに成功した。最新の採用済みfull DB gateは2,568 collected／2,568 passedで、failed／error／skipped／warning／xfailおよびpending task／unclosed resource／timeout／signalは各0。関連commitは通常push済み
- 実OpenAI Provider受入: 未実施
- 実Discord受入: AI無効表示、初期Mode／PreviewのCancel、ManualのPreview／Edit／Accept、Accept後のType Cancel、単発／毎日／毎週の予約作成と初回配信、stale ModalとEdit／Confirm／Cancel競合、2,000文字の入力から1回配信、Phase 4I確認画面と実配信のmention／Markdown／URL境界、予約種別選択ViewのOption A timeout、Bot本人のSend Messages権限喪失Option A、Bot再起動／recovery Option A、`@everyone`／`@here`の入力拒否まで確認済み。残る非AI Phase 4I受入は0件である
- ARM64 Linux実機受入: 未実施
- 本文生成feature flag: 初期無効を要件化、有効化不可
- Phase 4受入集計: 確認済み56件／未確認16件（合計72件）
- Stage Cと後続受入の反映: 実Discord AI・予約接続受入の各項目へ採用済み証跡を反映した。Phase 4I全体、実Provider、AI有効時の実Discord、ARM64 Linux、運用承認の完了を意味しない

Phase 4Aは要件・設計・運用・受入条件の確定だけを意味する。AI本文生成が利用可能、Providerが採用済み、費用・品質・保持が確認済み、または本番公開可能であることを意味しない。

## Phase 4D Migration実DB受入

- [x] 専用tmpfs PostgreSQL 18.4でrevision `c72e91f4b6a3`のupgrade、current、single heads、checkを確認した。
- [x] 新3 tableの14 CHECK制約名がORM metadataと完全一致し、hash付き短縮名、naming conventionの二重prefix、63-byte超過がないことを確認した。
- [x] 空の新3 tableを持つDBで`a41f8c7d2e90`へのdowngradeとheadへの再upgradeを確認した。
- [x] 新3 tableへ匿名合成行を1件ずつ独立して置いた場合、downgradeが固定文言で拒否され、revision、schema、対象データが保持されることを確認した。
- [x] 最終状態で新3 tableと既存8業務tableの計11業務tableが各0件であり、既存DB・Volumeへ影響せず、専用containerが`Exited (0)`となったことを確認した。
- [x] この検証でOpenAI通信を行っていないことを確認した。

検証手順には逸脱があった。最終確認中にmodule指定を誤ってBot入口を一度起動し、Discord clientの初期化ログが出た。直後にBot processが存在しないことを確認したが、Discord接続または投稿が成功したとは確認していない。また、ORM比較スクリプトの初回失敗時に検証専用の合成DB URLが例外へ一度表示された。実credential、既存`.env`、実データは表示されていない。このため、本受入は「Bot未実行」または「値非表示」の証拠とはせず、実Provider・実Discord受入の完了根拠にも使用しない。これらの逸脱はMigrationのschema、upgrade、downgrade、データ保持に対して別途取得した直接証拠を無効にしない。

## Phase 4H前半 無効runtime隔離受入

commit `cf34dac4ca7d2f65ebfbcc2d1c16a7e36e777c90`を専用tmpfs PostgreSQL 18.4で検証した。Migration current／single headは`c72e91f4b6a3`、Alembic checkは`No new upgrade operations detected.`で、11業務tableは欠落・余分なく、Migration直後、各DB test段階後、終了時のすべてで各0件だった。

- Stage 1 Runtime／Bot runtime／Post command／Discord UI／UI session／Composition: 397 passed
- Stage 2 残りのPostDraft DB非依存test: 455 passed
- Stage 3 Usage Repository＋cleanup integration: 26 passed
- Stage 4 PostgreSQL integration全体: 375 passed
- Stage 5 DB URLなし通常pytest: 1,633 passed、375 skipped
- failed、warning、想定外skip: 0
- container: `Exited (0)`。`/var/lib/postgresql`はtmpfsで、mount／named Volumeなし。既存DB／Volume／実データへの影響および秘密値の表示・証跡残存なし

r2の空DB probe失敗はsandboxのloopback socket制限によるものとの推定であり、loopback TCP接続を明示的に許可したr3では同じ空DB probeが成功した。PostgreSQL設定またはMigration不具合の証拠ではない。本受入ではBot、Gateway、Discord HTTP、OpenAI、command syncを実行しておらず、実Discord画面、実Provider、AI有効状態、DB保存・予約確定・投稿、cleanup定期実行、ARM64 Linux実機は未確認である。

## Phase 4H 実Discord AI無効・Manual受入

開発・検証専用Application／Guildで、Provider gateとAI機能を無効のままguild限定`/post compose`を確認した。全画面はephemeralであり、入力には匿名の合成テストデータだけを用いた。これはAI有効end-to-end、実Provider、予約保存・確定・投稿、一般提供または本番の受入ではない。

- [x] guild限定commandが実Discordに存在し、global sync 0回で、command定義の意味上の追加／変更／削除が各0件であることを確認した。
- [x] AI buttonが「AIで作成（準備中）」としてdisabledであり、OpenAI client構築／通信／AI workerが各0件であることを確認した。
- [x] 初期ModeのCancelでdefer、Controller cancel、cancelled遷移、original response更新が成功し、timeout表示がなく、componentが無効化または消去され、固定失敗event 5種類が各0件であることを確認した。
- [x] Mode → Manual Modal → Preview → Edit Modal → Preview → Acceptを実Discordで完了し、全画面がephemeralで公開channel投稿がないことを確認した。
- [x] 編集後Previewは現在本文だけを表示し、変更していない行を保持して旧本文履歴を表示しないことを確認した。
- [x] 「この本文を使用」を1回だけ操作するとcomponentが消え、採用完了と「まだ予約・投稿されていない」旨が表示されることを確認した。
- [x] Manual経路のgeneration service、Usage reserve、DB Session、予約保存、予約確定、投稿処理が各0件で、Migration `c72e91f4b6a3`、single head、Alembic check成功、起動前後の11業務table各0件、想定外table 0件を確認した。
- [x] 終了時にsupervisor exit code 0、cleanup成功、Bot／supervisor／container／listener停止を確認した。Bot childはSIGINTで終了しprivateの正常停止markerが成立しなかったが、process／container停止失敗または本受入不合格とはせず、private supervisorの終了観測改善候補として残す。

### Phase 4H Preview安全境界回帰受入

- [x] Manual PreviewとEdit後Previewで同じ表示変換を1回だけ適用し、通常HTTP(S) URLの対象文字、LF改行、日本語、Unicode、絵文字を保持しながら、確認したMarkdown link、装飾、code、list、user／nickname user／role／channel mention形式をliteral表示することを実Discordで確認した。変換対象はPreviewの`Embed.description`だけで、表示用backslash／U+200BはDomain、Session、Edit Modal初期値、Accept本文へ混入せず、二重escapeもない。
- [x] `@everyone`／`@here`を含むManual本文がDomain validationで拒否され、Previewへ進まず、入力を反射しない固定案内のinitial response attempt／success／normal returnが各1件、応答後の`is_done()`がtrueであり、followup、original response編集、2回目response、Modal `on_error`、timeout、mention通知、公開投稿、`view_error_response_failed`が各0件であることを実Discordで確認した。
- [x] Preview画面のCancelを1回操作し、defer attempt／success、Controller cancel、original response更新attempt／success、callback正常終了が各1件、claim成功、最終stateが`cancelled`でcomponentが消え、timeout、二重Cancel、stale callback、公開投稿、固定失敗event 5種類が各0件であることを実Discordで確認した。

複合表示には匿名の合成入力だけを用いた。通常HTTP(S) URLはクリック可能な文字列として保持し、確認した`_`、`*`、`~`、query、fragmentを変更しない。Markdown linkは表示名で遷移先を隠すlinkとして成立させず、bold、italic、strike、inline code、hyphen／numbered listもliteral表示した。user／nickname user／role／channel mention形式はDiscord上の名前、role、channel linkへ変換せず、通知も発生しなかった。これを全Markdown、全URL、IPv6 literal URL、実在ID、または将来の公開投稿の安全性へ一般化しない。`AllowedMentions.none()`による通知抑止、表示用escape、ephemeral表示は別々の境界である。

自動検証は通常pytest 1,781 passed／375 skipped、warning 0である。Preview scannerは入力上限2,000文字に対してPython `len`とUTF-16 code unitの理論最大がともに4,000で、Embed description上限4,096以内、truncate／欠落なし、時間・追加メモリともO(N)である。このPhase 4H回帰受入時点では、2,000／2,001文字境界は自動テストだけで確認し、実Discord確認済みとは扱わなかった。回帰受入でもgeneration、Usage reserve、DB保存、予約保存、予約確定、投稿処理、OpenAI client／通信／AI workerは各0件で、通常開発DBへ接続していない。隔離DBはrevision `c72e91f4b6a3`、11業務table各0件、想定外table 0件を保ち、終了時はsupervisor、Bot child、`postgres_test`がexit code 0、cleanup成功で全process、container、listenerが停止した。

受入実行中にproduction code、test、Migration、設定の変更は行っていない。本項の運用上のsync累計は製品仕様ではない。

## Phase 4I 予約引渡し自動受入

Phase 4Hの実Discord受入記録は、その時点でAccept後の予約接続が存在しなかった履歴として変更しない。Phase 4Iでは、そのaccepted terminal contractを変更せずに独立Schedule Sessionを構成し、現在本文だけを既存予約作成境界へ引き渡す実装を追加した。本項の結果は自動テストと専用PostgreSQLによる確認であり、実Discordの画面、予約保存、配信を確認した証拠ではない。

- DBなし通常pytest: 2,048 passed／393 skipped
- DB付き通常pytest: 2,441 passed
- PostgreSQL integration: 397 passed
- 冪等な予約作成: 22 passed
- Migration: revision `c72e91f4b6a3`のupgrade、current、single heads、checkおよび接続確認に成功
- 品質検査: Ruff check、Ruff format check、通常差分・staged差分のcheckに成功
- 終了時監査: 11業務table各0行、想定外table 0、connection／transaction／lock／task leak 0、secret reflection 0
- 隔離project: 専用資材のcleanupに成功し、code commitと通常pushを完了

Migration safety wrapperとAlembic環境は、秘密情報を連結しない固定stage markerと固定failure categoryだけを出力する。例外message、URL、credential、DB識別子、SQL本文を診断出力へ反射しない。

## Phase 4I Stage C 採用済み受入

Stage CはPhase 4Iの受入作業で用いた区分であり、正式計画に独立したStage DまたはPhase 4Jを追加するものではない。次の証拠は、手動観測と自動integrationを分離したうえで採用する。

- 実Discord手動観測: Type Cancelが意図しない予約・投稿を作らないことを確認した。単発は既存形式の日本時間入力から予約作成と初回配信1件まで、毎日は予約作成と初回配信1件まで、毎週は完全な日本語曜日名の入力受理、予約作成、初回配信1件まで確認した。
- PostgreSQL integration: 単発は日本時間入力からUTC保存／本文handoff、作成、初回配信、DB lifecycleを確認した。毎日はScheduleがactiveのまま初回Runがsucceededとなり、翌日同時刻のRunがpendingとなることを確認した。毎週は同一Scheduleについてactive、初回Run succeeded、翌週同曜日・同時刻のRun pendingを含む必須22条件を確認した。
- 監査境界: `tests/support/read_only_database_audit.py`のfake boundary validationと実PostgreSQL integrationに成功した。手動観測をDB結果へ推測で置換せず、DB lifecycleとDiscord上の公開投稿を別の証拠として扱う。
- 最終gate: 2,567 collected／2,567 passed。failed／error／skipped／warning／xfailおよびpending task／unclosed resource／timeout／signalは各0で、関連commitの通常push、Gitのlocal／origin同期、隔離資材のcleanupまで完了した。

### Stage C後の非AI受入

- stale Modalと競合操作: Confirm成功と古いEdit Modalのstale表示を各1件観測し、Schedule／Run／DeliveryAttempt／OperationLogが1／1／0／1、staleによる追加副作用と公開投稿が各0であることをread-only DB監査と分離して確認した。別SessionではCancel成功と古いEdit Modalのstale表示を各1件観測し、追加Schedule／Run／DeliveryAttempt／OperationLogが0／0／0／0、既存graph不変、公開投稿0を確認した。最初に有効な状態遷移を取得した操作だけが勝つcontractを、実Discord表示とread-only DB監査の合成証跡で確認済みとする。
- 2,000文字境界: 手入力Modal、Preview、本文採用、単発予約を実Discordで完了し、公開投稿1件、分割／重複／目視上の欠落／独自prefix・suffix／エラー各0を観測した。read-only PostgreSQL監査ではPython／DB本文長が2,000／2,000で合成本文と完全一致し、Schedule／Run／DeliveryAttempt／OperationLogが1／1／1／2、配信成功、retry 0だった。別のintegration characterizationと最新full DB 2,568／2,568成功も採用する。
- mention／Markdown／URL境界: Previewでは完全mention tokenとMarkdown記号をliteral表示し、malformed mentionを部分変換しなかった。公開投稿は1件で分割／重複／エラー各0、合成mentionは不明な対象として視覚変換され、予期しない通知とmention警告は0、Markdown 10形式は通常処理され、Markdown link、通常URL、angle URLを表示した。`AllowedMentions`のeveryone／users／roles／replied_userがすべてfalseである自動テストと実Discord観測を合成証跡として採用する。合成IDを用いたため実在対象へのpush通知試験ではなく、URL unfurlは現行contract外である。
- 予約種別選択View timeout（Option A）: production設定の900秒以上待機した後にCancelを1回だけ操作し、Discord標準のgeneric interaction failure表示、公開投稿0、二重操作0を実Discordで確認した。Bot独自の日本語timeout表示とcomponentの視覚的disableは本contractの対象外とする。別のread-only DB監査はharness validでFALSE／UNKNOWN各0、Schedule／ScheduleRun／DeliveryAttempt／OperationLogとName generation／Post Draft usage関連row、有効な状態遷移が各0だった。sync／OpenAI request／retry各0は設定と操作の証跡として分離して採用し、cleanupまで完了した。DB lock timeout、Discord network timeout、provider timeoutは別責務である。
- Bot Send Messages権限喪失（Option A）: 開発専用投稿先channelに対するBot本人のSend Messagesだけをdenyし、guild Bot role、`@everyone`、operator role、operator通知channel、他権限は変更しなかった。単発Post Draftの対象channel公開投稿は0、operator通知は1件で重複0だった。read-only DB監査ではSchedule／Run／DeliveryAttempt／OperationLogが1／1／1／2で、ScheduleとRunはfailed、Runは`RESULT_FAILED`かつ`next_attempt_at = None`、DeliveryAttemptはfailed／permanent、OperationLogはcreated→failedだけだった。retry／duplicate／unknown／internal errorは各0である。変更前のchannel overwriteへ手動で完全復元し、復元後監査でも新規Run／DeliveryAttemptとblind retry／再送が各0であることを確認した。実Discord観測、DB証拠、手動復元を分離して採用し、cleanupまで完了した。production code／testは変更していない。userのView Channel、Embed Links、operator role、guild membership喪失は本Optionの対象外である。
- Bot再起動／recovery（Option A）: future once予約を作成し、配信前にforeground BotへCtrl+Cを1回送って正常停止した。DBとDockerを保持したまま、保存済みhashと一致する同一launcherを1回だけ再実行し、startup recoveryがworker pollより先に完了したことを確認した。停止前後のread-only DB監査ではSchedule active／Run pendingのgraphが不変だった。実Discordでは公開投稿1件・重複0件を観測し、配信後のread-only DB監査ではSchedule completed、Run succeeded／`RESULT_SUCCEEDED`、DeliveryAttempt 1件 succeeded、OperationLog created→completedだけで、retry／failed／unknown／internal errorは各0だった。実Discord手動観測、read-only DB証拠、launcher hash、cleanup証拠を分離して採用し、production code／testを変更せずcleanupまで完了した。
- 証拠境界: mention／Markdown／URL配信後の個別DB監査はquery callback構築不備で全DB条件がUNKNOWNとなり、DB不一致の証拠はなかった。このUNKNOWNをPASSへ変更せず、application不良としても扱わない。raw本文の保存・配信には既存raw handoff、Discord gateway、2,000文字実PostgreSQL lifecycleの証拠を採用する。
- 最新gate: 2,568 collected／2,568 passed。failed／error／skipped／warning／xfailおよびpending task／unclosed resource／timeout／signalは各0で、関連commitの通常push、Gitのlocal／origin同期、各隔離資材のcleanupまで完了した。

残るPhase 4I非AI受入は0件である。Real Provider、AI有効時の実Discord、ARM64 Linux、運用承認は本文生成feature flag有効化前の別gateとして残し、Phase 4全体または製品リリースの完了とは扱わない。

`AI_POST_DRAFT_ENABLED=false`、`AI_NAME_GENERATION_ENABLED=false`、`AI_NAME_GENERATION_PROVIDER=disabled`を維持する。上記の別gateが完了し、運用者が明示承認するまで変更しない。release／merge前には、その時点の新しいtipでCIを通す。

## 利用回数・費用上限の未決事項

現在のuser 3回／固定10分、guild 30回／JST日、global 50回／JST日・500回／JST月、月次悲観費用500円相当、およびuser bucket 7日、guild bucket 30日、operator Budget 90日、receipt 7日の保持期間は、実装と安全検証に用いる暫定値である。正式な商品仕様、サブスクリプション仕様、一般提供時の確定値または販売上の約束ではなく、正式承認を待つ。

実Provider価格、テスト運用、収益性、プラン設計を確認して再決定し、feature有効化前に必ず再監査する。プラン別利用枠と運営上の安全rate limitは別に管理し、上位プランも運営全体の安全上限を回避できないものとする。Plan／Entitlementに基づくプラン別利用回数は未実装であり、将来は設定とDB上のPlan／Entitlementから変更可能にする。Free、Standard、Pro等の名称と具体的回数は現時点で定めない。

## 4A 文書受入

- [x] AIの責務を本文下書き生成だけとし、自動保存・予約・投稿を禁止する。
- [x] 新しい`/post compose`を入口とし、既存予約コマンドと本文手入力方式を維持する。
- [x] 手入力とAI作成を選択可能にし、Provider障害時も手入力へ戻れるようにする。
- [x] 文体を「丁寧・親しみやすい・簡潔」、長さを「短め・標準・長め」の閉じた選択肢とする。
- [x] 目的1～200文字、要点1～1,000文字、生成本文・最終本文1～2,000文字を定義する。
- [x] Providerへ送る情報と送らない識別情報・秘密情報を定義する。
- [x] 条件、prompt、AI原文、生成履歴を保存せず、編集・確認済み最終本文だけを予約確定時に保存する。
- [x] 「予約する」より前はDB保存・Discord投稿を行わない。
- [x] user／guild rate limitと永続的な運営Budgetを分離して設ける。
- [x] 再生成、timeout、cancel後の結果不明、Provider結果不明を安全上1回分として扱う。
- [x] URL・Markdownを許可し、`@everyone`、`@here`、危険な制御文字・bidi文字を拒否する。
- [x] Moderation API、自動retry、fallback modelをMVP対象外とする。
- [x] feature flagを初期無効とし、実Provider・実Discord・ARM64 Linux実機受入完了まで有効化しない。
- [x] Phase 3の125／127と6C 4／4を変更せず、Phase 4を別管理する。

## 実装・自動隔離受入

- [x] Provider非依存のrequest、result、文体、長さ、本文validationを実装する。
- [x] disabled境界、one-shot生成、timeout、cancel、typed error、retryなしを実装する。
- [x] user／guild rate limitと永続的な運営Budgetを実装する。
- [x] 目的、要点、条件、prompt、AI原文、履歴がDBと通常logへ保存されないことを確認する。
- [x] `/post compose`の注意、入力、生成、編集、再生成、本文採用、および本文採用後の予約最終確認を実装し、DBなし通常pytest 2,048 passed／393 skippedとDB付き通常pytest 2,441 passedの一部として確認する。
- [x] owner／guild／channel認可と、stale View、二重押下、Edit／Confirm／Cancel競合による重複保存・投稿の抑止を自動テストで確認する。
- [x] 「予約する」前のSchedule、Run、OperationLog増加が0件であることを確認する。
- [x] 利用者が編集・確認した最終本文だけが、accepted terminal contractから独立Schedule Sessionを経て既存予約作成Serviceへ渡ることを確認する。
- [x] 単発・毎日・毎週の予約と既存手入力コマンドが回帰していないことを確認する。
- [x] AI disabled、Provider disabled、Budget超過、rate limit、timeout、障害時も通常予約が利用できることを確認する。
- [x] API key、Discord token、DB URL、Provider payload／response、本文、例外全文のlog非露出をcanaryで確認する。
- [x] revision `c72e91f4b6a3`について、専用tmpfs PostgreSQLでupgrade、current、heads、check、空DB downgrade、データ存在時のdowngrade拒否と既存schema非破壊を確認し、PostgreSQL integration 397 passedと冪等作成22 passedを確認する。
- [x] 予約入力validation失敗時に直前の有効な入力を保持し、利用者が再入力できることを自動テストで確認する。
- [x] public ID生成と予約作成Port呼出しを各最大1回とし、`created`／`already_created`／`conflict`／`unknown`を固定結果へ写像し、`unknown`後に再INSERT・retryしないことを確認する。
- [x] Discord transport／render／response失敗を固定eventへ閉じ、秘密情報、本文、任意の例外内容を反射しないことを確認する。
- [x] Migrationの到達stageと失敗分類を固定marker／固定categoryだけで観測し、通常logと診断出力へ秘密情報を反射しないことを確認する。

## 実Provider受入

- [ ] 実行直前にモデル提供状態、単価、Responses API、structured output、保持、ZDR、国内処理、SDK対応を公式情報で再監査する。
- [ ] 専用Project、制限付きAPI key、Project予算・アラート、最大request数、悲観最大費用、Auto-recharge状態を確認する。
- [ ] 個人情報、実ID、実URL、実本文、秘密情報を含まない固定匿名ケースだけを使用する。
- [ ] retry、fallback、Batch、並列実行、自動保存を行わず、各requestを明示的に1回だけ実行する。
- [ ] 日本語品質、3文体、3長さ、1～2,000文字、URL、Markdown、mention、制御文字、bidi、prompt injection風入力を確認する。
- [ ] timeout、usage、請求、保持、dashboard条件を確認し、実結果をDB・追跡ファイル・通常logへ保存しない。

## 実Discord AI・予約接続受入

- [x] Manual本文のAccept後に予約種別選択画面が実Discordで表示される。
- [x] 単発／毎日／毎週について、実Discordで予約条件を入力・編集し、最終確認から確定できる。
- [x] 実Discordの確定操作に対応する予約が正確に保存され、予定時刻に正確に1回配信される。
- [x] stale Modalと競合するEdit／Confirm／Cancelが実Discordで重複保存・投稿を起こさず、最初に有効な状態遷移を取得した操作だけが反映されることを確認する。
- [x] 2,000文字境界を実Discordの入力、確認、予約保存、配信まで確認する。
- [ ] 本番Applicationまたは一般利用者環境での挙動を、開発・検証専用環境の結果と分離して確認する。
- [ ] `/post compose`で手入力とAI作成を選べる。
- [ ] Provider送信前にprivacy、誤り、利用枠、悲観費用がephemeral表示される。
- [ ] AI下書きを編集・再生成でき、最終確認に投稿先・日時・本文・AI利用が表示される。
- [x] 予約種別選択Viewをproduction設定の900秒以上保持し、timeout後の操作が予約・投稿・利用枠副作用を生じないOption A contractを実Discord観測とread-only DB監査で確認する。
- [x] 開発専用投稿先channelでBot本人のSend Messagesだけをdenyし、単発配信がfailed／permanentで終端して公開投稿、retry、blind retryを生じず、権限復元後も同じRunを再送しない権限喪失Option Aと、future once予約の配信前にgraceful停止し、同一hashのlauncherで再起動してworker poll前のstartup recovery後に1回だけ配信するBot再起動／recovery Option Aを確認する。
- [ ] AI障害時に既存予約コマンドと手入力経路を利用できる。
- [x] Phase 4I確認表示でmention／Markdownをliteral表示し、実際の配信で`AllowedMentions`による通知抑止とMarkdown／URLの定義済み表示境界を確認する。

## ARM64 Linux実機受入

- [ ] 配置先候補と同等のARM64 Linux実機で依存解決、import、Mock transport、Provider Adapter shutdownを確認する。
- [ ] 実Discordを使用する場合は実Provider受入と別の明示手順・費用境界で実施する。

## feature flag有効化gate

次をすべて満たすまで本文生成feature flagを有効化しない。

実装・自動隔離受入の完了、およびPostgreSQL統合とMigration受入の完了は、上記の対応する詳細行へ証跡件数とともに統合済みである。独立したcheckboxとして再計上しない。

- [ ] 実Provider受入が完了している。
- [ ] 実Discord受入が完了している。
- [ ] ARM64 Linux実機受入が完了している。
- [ ] 運用者がモデル、価格、利用枠、保持、障害対応を明示承認している。
