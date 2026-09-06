# Phase 4 AI投稿本文下書き受入

AI投稿本文下書きをPhase 3から分離して管理する。Phase 3の確認済み125件／未確認2件（合計127件）および第6項6Cの4件／4件は変更しない。本書のチェックはPhase 4の証跡だけで更新し、文書化、実装、自動隔離テスト、PostgreSQL統合、実Provider、実Discord、ARM64 Linux実機を混同しない。

## 現在の判定

- Phase 4A 文書化: 完了
- Provider非依存Domain型とvalidation、one-shot Application Service、Usage Repository Port、Budget／rate limit／receipt Domain: 実装・自動隔離テスト済み
- 本文専用ORM schema: `post_draft_operator_budget_buckets`、`post_draft_rate_limit_buckets`、`post_draft_usage_reservation_receipts`の3 tableとrevision `c72e91f4b6a3`を実装・実DB検証済み
- PostgreSQL Usage Repository、Usage reservation orchestration、Usage cleanup、独立Usage Settings、無効Composition、production未接続のOpenAI Responses API Adapter、UI Session／Controller、Discord UI部品、`PostDraftRuntime`: 実装・自動隔離テスト済み
- `/post compose`: 既存guild限定`/post` Groupへ登録し、開発・検証専用Application／Guildでguild限定command、AI無効表示、初期Mode／PreviewのCancel、ManualのPreview／Edit／Accept、確認表示の限定したescape境界を実Discord確認済み。global command syncは行っていない
- production Composition: Provider gateはfalseで`DisabledPostDraftGenerator`を使用し、Provider Settings loader、`AsyncOpenAI`、OpenAI Adapterは未接続。AI buttonはdisabledの「AIで作成（準備中）」表示
- Manualフロー: Preview／Edit／Acceptまで自動隔離・実Discord確認済み。Previewの`Embed.description`だけを表示用に変換し、Domain、Session、Edit Modal初期値、Accept本文はrawのまま保持する。Usage予約、DB保存、予約確定、channel投稿へは未接続で、採用後も「まだ予約・投稿されていない」と表示する
- Usage cleanupのruntime wiring／定期実行、予約確定フローとの接続、Plan／Entitlementとプラン別利用枠: 未実装
- 正式model、価格・費用承認、正式UI timeout: 未決定
- 自動隔離テストとPostgreSQL統合テスト: commit `cf34dac4ca7d2f65ebfbcc2d1c16a7e36e777c90`で下記の隔離runtime受入を完了
- 実OpenAI Provider受入: 未実施
- 実Discord受入: AI無効表示、初期Mode／PreviewのCancel、ManualのPreview／Edit／Accept、確認表示の限定したURL・Markdown・mention境界、`@everyone`／`@here`の入力拒否まで確認済み。実Provider、AI生成／再生成、2,000文字境界、予約保存／確定、投稿は未確認
- ARM64 Linux実機受入: 未実施
- 本文生成feature flag: 初期無効を要件化、有効化不可
- Phase 4受入集計: 確認済み40件／未確認23件（合計63件）

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

自動検証は通常pytest 1,781 passed／375 skipped、warning 0である。Preview scannerは入力上限2,000文字に対してPython `len`とUTF-16 code unitの理論最大がともに4,000で、Embed description上限4,096以内、truncate／欠落なし、時間・追加メモリともO(N)である。2,000／2,001文字境界は自動テストだけで確認し、実Discord確認済みとは扱わない。回帰受入でもgeneration、Usage reserve、DB保存、予約保存、予約確定、投稿処理、OpenAI client／通信／AI workerは各0件で、通常開発DBへ接続していない。隔離DBはrevision `c72e91f4b6a3`、11業務table各0件、想定外table 0件を保ち、終了時はsupervisor、Bot child、`postgres_test`がexit code 0、cleanup成功で全process、container、listenerが停止した。

受入実行中にproduction code、test、Migration、設定の変更は行っていない。本項の運用上のsync累計は製品仕様ではない。

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
- [ ] `/post compose`の注意、入力、生成、編集、再生成、本文採用、最終確認を実装する。
- [ ] 二重押下、期限切れ、権限喪失、Bot shutdown／restartで重複生成・保存・投稿しないことを確認する。
- [x] 「予約する」前のSchedule、Run、OperationLog増加が0件であることを確認する。
- [ ] 利用者が編集・確認した最終本文だけが既存予約作成Serviceへ渡ることを確認する。
- [x] 単発・毎日・毎週の予約と既存手入力コマンドが回帰していないことを確認する。
- [x] AI disabled、Provider disabled、Budget超過、rate limit、timeout、障害時も通常予約が利用できることを確認する。
- [x] API key、Discord token、DB URL、Provider payload／response、本文、例外全文のlog非露出をcanaryで確認する。
- [x] revision `c72e91f4b6a3`について、専用tmpfs PostgreSQLでupgrade、current、heads、check、空DB downgrade、データ存在時のdowngrade拒否と既存schema非破壊を確認する。

## 実Provider受入

- [ ] 実行直前にモデル提供状態、単価、Responses API、structured output、保持、ZDR、国内処理、SDK対応を公式情報で再監査する。
- [ ] 専用Project、制限付きAPI key、Project予算・アラート、最大request数、悲観最大費用、Auto-recharge状態を確認する。
- [ ] 個人情報、実ID、実URL、実本文、秘密情報を含まない固定匿名ケースだけを使用する。
- [ ] retry、fallback、Batch、並列実行、自動保存を行わず、各requestを明示的に1回だけ実行する。
- [ ] 日本語品質、3文体、3長さ、1～2,000文字、URL、Markdown、mention、制御文字、bidi、prompt injection風入力を確認する。
- [ ] timeout、usage、請求、保持、dashboard条件を確認し、実結果をDB・追跡ファイル・通常logへ保存しない。

## 実Discord AI・予約接続受入

- [ ] `/post compose`で手入力とAI作成を選べる。
- [ ] Provider送信前にprivacy、誤り、利用枠、悲観費用がephemeral表示される。
- [ ] AI下書きを編集・再生成でき、最終確認に投稿先・日時・本文・AI利用が表示される。
- [ ] 「予約する」以外の操作、timeout、Bot再起動では予約も投稿も行われない。
- [ ] AI障害時に既存予約コマンドと手入力経路を利用できる。
- [ ] 実際の配信でmentionが展開されず、確認表示のMarkdownが安全である。

## ARM64 Linux実機受入

- [ ] 配置先候補と同等のARM64 Linux実機で依存解決、import、Mock transport、Provider Adapter shutdownを確認する。
- [ ] 実Discordを使用する場合は実Provider受入と別の明示手順・費用境界で実施する。

## feature flag有効化gate

次をすべて満たすまで本文生成feature flagを有効化しない。

- [ ] 実装・自動隔離受入が完了している。
- [ ] PostgreSQL統合とMigration受入が完了している。
- [ ] 実Provider受入が完了している。
- [ ] 実Discord受入が完了している。
- [ ] ARM64 Linux実機受入が完了している。
- [ ] 運用者がモデル、価格、利用枠、保持、障害対応を明示承認している。
