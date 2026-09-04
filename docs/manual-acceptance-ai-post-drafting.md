# Phase 4 AI投稿本文下書き受入

AI投稿本文下書きをPhase 3から分離して管理する。Phase 3の確認済み125件／未確認2件（合計127件）および第6項6Cの4件／4件は変更しない。本書のチェックはPhase 4の証跡だけで更新し、文書化、実装、自動隔離テスト、PostgreSQL統合、実Provider、実Discord、ARM64 Linux実機を混同しない。

## 現在の判定

- Phase 4A 文書化: 完了
- Provider非依存Domain型とvalidation、one-shot Application Service、Usage Repository Port、Budget／rate limit／receipt Domain: 実装・自動隔離テスト済み
- 本文専用ORM schema: `post_draft_operator_budget_buckets`、`post_draft_rate_limit_buckets`、`post_draft_usage_reservation_receipts`の3 tableとrevision `c72e91f4b6a3`を実装・実DB検証済み
- Phase 4本文下書き用PostgreSQL Usage Repository、Usage reservation orchestration、cleanup、実行時設定、OpenAI本文Adapter、Discord UI、予約確定フローとの接続、Plan／Entitlementとプラン別利用枠: 未実装
- 自動隔離テスト: Domain／Application／schemaの実装済み範囲で実施済み
- PostgreSQL統合テスト: Migration schemaの実装済み範囲で実施済み（下記の逸脱を含む）
- 実OpenAI Provider受入: 未実施
- 実Discord受入: 未実施
- ARM64 Linux実機受入: 未実施
- 本文生成feature flag: 初期無効を要件化、有効化不可

Phase 4Aは要件・設計・運用・受入条件の確定だけを意味する。AI本文生成が利用可能、Providerが採用済み、費用・品質・保持が確認済み、または本番公開可能であることを意味しない。

## Phase 4D Migration実DB受入

- [x] 専用tmpfs PostgreSQL 18.4でrevision `c72e91f4b6a3`のupgrade、current、single heads、checkを確認した。
- [x] 新3 tableの14 CHECK制約名がORM metadataと完全一致し、hash付き短縮名、naming conventionの二重prefix、63-byte超過がないことを確認した。
- [x] 空の新3 tableを持つDBで`a41f8c7d2e90`へのdowngradeとheadへの再upgradeを確認した。
- [x] 新3 tableへ匿名合成行を1件ずつ独立して置いた場合、downgradeが固定文言で拒否され、revision、schema、対象データが保持されることを確認した。
- [x] 最終状態で新3 tableと既存8業務tableの計11業務tableが各0件であり、既存DB・Volumeへ影響せず、専用containerが`Exited (0)`となったことを確認した。
- [x] この検証でOpenAI通信を行っていないことを確認した。

検証手順には逸脱があった。最終確認中にmodule指定を誤ってBot入口を一度起動し、Discord clientの初期化ログが出た。直後にBot processが存在しないことを確認したが、Discord接続または投稿が成功したとは確認していない。また、ORM比較スクリプトの初回失敗時に検証専用の合成DB URLが例外へ一度表示された。実credential、既存`.env`、実データは表示されていない。このため、本受入は「Bot未実行」または「値非表示」の証拠とはせず、実Provider・実Discord受入の完了根拠にも使用しない。これらの逸脱はMigrationのschema、upgrade、downgrade、データ保持に対して別途取得した直接証拠を無効にしない。

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

- [ ] Provider非依存のrequest、result、文体、長さ、本文validationを実装する。
- [ ] disabled境界、one-shot生成、timeout、cancel、typed error、retryなしを実装する。
- [ ] user／guild rate limitと永続的な運営Budgetを実装する。
- [ ] 目的、要点、条件、prompt、AI原文、履歴がDBと通常logへ保存されないことを確認する。
- [ ] `/post compose`の注意、入力、生成、編集、再生成、本文採用、最終確認を実装する。
- [ ] 二重押下、期限切れ、権限喪失、Bot shutdown／restartで重複生成・保存・投稿しないことを確認する。
- [ ] 「予約する」前のSchedule、Run、OperationLog増加が0件であることを確認する。
- [ ] 利用者が編集・確認した最終本文だけが既存予約作成Serviceへ渡ることを確認する。
- [ ] 単発・毎日・毎週の予約と既存手入力コマンドが回帰していないことを確認する。
- [ ] AI disabled、Provider disabled、Budget超過、rate limit、timeout、障害時も通常予約が利用できることを確認する。
- [ ] API key、Discord token、DB URL、Provider payload／response、本文、例外全文のlog非露出をcanaryで確認する。
- [x] revision `c72e91f4b6a3`について、専用tmpfs PostgreSQLでupgrade、current、heads、check、空DB downgrade、データ存在時のdowngrade拒否と既存schema非破壊を確認する。

## 実Provider受入

- [ ] 実行直前にモデル提供状態、単価、Responses API、structured output、保持、ZDR、国内処理、SDK対応を公式情報で再監査する。
- [ ] 専用Project、制限付きAPI key、Project予算・アラート、最大request数、悲観最大費用、Auto-recharge状態を確認する。
- [ ] 個人情報、実ID、実URL、実本文、秘密情報を含まない固定匿名ケースだけを使用する。
- [ ] retry、fallback、Batch、並列実行、自動保存を行わず、各requestを明示的に1回だけ実行する。
- [ ] 日本語品質、3文体、3長さ、1～2,000文字、URL、Markdown、mention、制御文字、bidi、prompt injection風入力を確認する。
- [ ] timeout、usage、請求、保持、dashboard条件を確認し、実結果をDB・追跡ファイル・通常logへ保存しない。

## 実Discord受入

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
