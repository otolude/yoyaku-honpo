# Phase 4 AI投稿本文下書き受入

AI投稿本文下書きをPhase 3から分離して管理する。Phase 3の確認済み125件／未確認2件（合計127件）および第6項6Cの4件／4件は変更しない。本書のチェックはPhase 4の証跡だけで更新し、文書化、実装、自動隔離テスト、PostgreSQL統合、実Provider、実Discord、ARM64 Linux実機を混同しない。

## 現在の判定

- Phase 4A 文書化: 完了
- Python実装: 未実装
- Migration／DB実装: 未実装
- 自動隔離テスト: 未実施
- PostgreSQL統合テスト: 未実施
- 実OpenAI Provider受入: 未実施
- 実Discord受入: 未実施
- ARM64 Linux実機受入: 未実施
- 本文生成feature flag: 初期無効を要件化、有効化不可

Phase 4Aは要件・設計・運用・受入条件の確定だけを意味する。AI本文生成が利用可能、Providerが採用済み、費用・品質・保持が確認済み、または本番公開可能であることを意味しない。

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
- [ ] Migrationのupgrade、heads、check、安全なdowngradeと既存schema非破壊を確認する。

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
