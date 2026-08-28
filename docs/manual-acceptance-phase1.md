# Phase 1 手動受入チェックリスト

## 1. 使い方と安全境界

- `[x]`はこれまでに実Discordで確認済みと申告された項目だけを示す。
- `[ ]`は未確認であり、コードや自動テストの存在だけで完了扱いにしない。
- 実施時は「実施日」「実施者」「証跡」を更新し、期待結果との差異を記録する。
- 予約の識別には内部DB IDではなく、Discordに表示される完全なpublic UUIDv7を使う。
- 証跡へtoken、DATABASE_URL、DB password、投稿本文、内部例外全文を含めない。
- DB状態の破壊、lease中断、Rate Limit、unknown、fallback失敗、cleanup障害などの危険なシナリオは、開発DBや通常の開発guildではなく、専用テストDB、Fake Gateway、隔離した受入環境で行う。
- 本物のDiscordへ大量リクエストしてRate Limitやunknownを意図的に発生させない。
- 環境準備・停止は[運用Runbook](operations.md)に従う。

## 2. 確認済み項目

以下の実施日は過去確認時の記録が提示されていないため「記録なし」とする。証跡は本会話でのユーザー申告であり、追加の結果を推測していない。

| 状態・項目 | 目的 | 前提 | 操作 | 期待結果 | 実施日 | 実施者 | 証跡 | 後片付け |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [x] Botを開発用guildへインストール | guild限定導入の確認 | Discord applicationと開発guild | OAuth2でBotを導入 | Botが対象guildに存在する | 記録なし | ユーザー | 本会話での申告 | なし |
| [x] 必要なロールとID設定 | 認可設定の確認 | guild、許可ロール、operator | `.env`へ必要IDを設定 | 設定を使ってBotを起動できる | 記録なし | ユーザー | 本会話での申告 | 秘密値を共有しない |
| [x] Bot起動・ログイン | runtime接続の確認 | DB revision一致、設定済み | Botを起動 | BotがDiscordへログインする | 記録なし | ユーザー | 本会話での申告 | 正常停止 |
| [x] 単発予約作成 | 単発作成の確認 | 権限ある利用者 | `/post create`を完了 | 単発予約が作成される | 記録なし | ユーザー | 本会話での申告 | 必要なら予約削除 |
| [x] 単発予約の予定時刻投稿 | 実配送の確認 | 本文あり単発予約 | 予定時刻まで待つ | 1回だけ投稿される | 記録なし | ユーザー | 本会話での申告 | 投稿と予約を記録 |
| [x] 毎日予約の作成表示 | 定期作成UIの確認 | 権限ある利用者 | `/post create-daily` | 作成結果Embedが表示される | 記録なし | ユーザー | 本会話での申告 | テスト予約を削除 |
| [x] 毎週予約の作成表示 | 定期作成UIの確認 | 権限ある利用者 | `/post create-weekly` | 作成結果Embedが表示される | 記録なし | ユーザー | 本会話での申告 | テスト予約を削除 |
| [x] list | 一覧表示の確認 | 予約が存在 | `/post list` | 操作可能な予約がEmbed表示される | 記録なし | ユーザー | 本会話での申告 | なし |
| [x] show | 詳細表示の確認 | public UUIDv7を保持 | `/post show` | 対象予約の詳細がEmbed表示される | 記録なし | ユーザー | 本会話での申告 | なし |
| [x] edit | 編集の確認 | 編集可能な予約 | `/post edit` | 指定変更と結果Embedが反映される | 記録なし | ユーザー | 本会話での申告 | テスト予約を削除 |
| [x] delete確認画面 | 削除Viewの確認 | 削除可能な予約 | `/post delete` | 対象と削除・キャンセルが表示される | 記録なし | ユーザー | 本会話での申告 | 続く確認で処理 |
| [x] deleteキャンセル | 削除cancelの確認 | 削除View表示中 | キャンセルを押す | 予約が削除されない | 記録なし | ユーザー | 本会話での申告 | 必要なら別途削除 |
| [x] delete確定 | 論理削除の確認 | 削除View表示中 | 削除を押す | 予約がdeletedになる | 記録なし | ユーザー | 本会話での申告 | なし |
| [x] pause | 定期停止の確認 | active定期予約 | `/post pause` | pausedになり次回が解除される | 記録なし | ユーザー | 本会話での申告 | resumeまたは削除 |
| [x] resume | 定期再開の確認 | paused定期予約 | `/post resume` | 未来の次回runで再開する | 記録なし | ユーザー | 本会話での申告 | テスト予約を削除 |
| [x] draft作成直後のcreator DM | immediate通知の確認 | 1時間未満のdraft、DM可 | draftを作成 | creatorへDM Embedが届く | 記録なし | ユーザー | 本会話での申告 | テスト予約を削除 |
| [x] draft到来時のoperator通知 | 見送り通知の確認 | draftが予定時刻へ到来 | 予定時刻まで待つ | 投稿せずoperatorへ通知される | 記録なし | ユーザー | 本会話での申告 | テスト予約を削除 |
| [x] 投稿権限拒否による最終failed通知 | permanent failure通知の確認 | 投稿権限のない専用チャンネル | 本文あり予約を到来させる | 投稿せず最終failed通知が届く | 記録なし | ユーザー | 本会話での申告 | 権限と予約を戻す |
| [x] 通知Embed | 通知形式の確認 | 通知イベント発生 | 通知を表示 | Discord Embedである | 記録なし | ユーザー | 本会話での申告 | なし |
| [x] 通知の日本語状態名 | 表示言語の確認 | 通知Embed表示 | 状態欄を確認 | 状態が日本語で表示される | 記録なし | ユーザー | 本会話での申告 | なし |
| [x] 通知上のチャンネルメンション | 投稿先表示の確認 | 通知Embed表示 | 投稿先欄を確認 | `#チャンネル`として解決可能に表示される | 記録なし | ユーザー | 本会話での申告 | なし |
| [x] 従来の日時形式 | 互換入力の確認 | `/post create` | `YYYY-MM-DD HH:MM`等を入力 | 正しい完全日時として受理される | 記録なし | ユーザー | 本会話での申告 | テスト予約を削除 |
| [x] `今日 HH:MM` | 自然日付入力の確認 | 5分以上先 | 空白あり形式を入力 | 当日JSTとして受理される | 記録なし | ユーザー | 本会話での申告 | キャンセルまたは削除 |
| [x] `明日 HH:MM` | 自然日付入力の確認 | 有効時刻 | 空白あり形式を入力 | 翌日JSTとして受理される | 記録なし | ユーザー | 本会話での申告 | キャンセルまたは削除 |
| [x] `今日HH:MM` | 短縮入力の確認 | 5分以上先 | 空白なし形式を入力 | 当日JSTとして受理される | 記録なし | ユーザー | 本会話での申告 | キャンセルまたは削除 |
| [x] `明日HH:MM` | 短縮入力の確認 | 有効時刻 | 空白なし形式を入力 | 翌日JSTとして受理される | 記録なし | ユーザー | 本会話での申告 | キャンセルまたは削除 |
| [x] 年省略形式 | 次回実在日時の確認 | `/post create` | `M/D HH:MM`を入力 | 次に到来する実在日時で表示される | 記録なし | ユーザー | 本会話での申告 | キャンセルまたは削除 |
| [x] 作成確認Embed | 保存前確認の確認 | 有効な単発入力 | `/post create` | 入力値、完全日時、予約・キャンセルが表示される | 記録なし | ユーザー | 本会話での申告 | 続く確認で処理 |
| [x] 作成キャンセル | 作成cancelの確認 | 作成確認View表示中 | キャンセルを押す | キャンセル結果が表示される | 記録なし | ユーザー | 本会話での申告 | なし |
| [x] 作成キャンセル後に予約が追加されない | DB副作用なしの確認 | 作成をキャンセル済み | list等で確認 | 対象予約が存在しない | 記録なし | ユーザー | 本会話での申告 | なし |
| [x] 全角数字・記号の専用エラー | 原因別案内の確認 | `/post create` | 全角数字・記号を入力 | 半角入力案内がephemeral表示される | 記録なし | ユーザー | 本会話での申告 | なし |
| [x] 一般形式不正エラー | 形式案内の確認 | `/post create` | 未対応形式を入力 | 日時形式案内がephemeral表示される | 記録なし | ユーザー | 本会話での申告 | なし |
| [x] 日時オプション説明 | Discord option表示の確認 | guild command同期済み | `/post create`の説明を見る | 短縮された半角入力説明が表示される | 記録なし | ユーザー | 本会話での申告 | なし |
| [x] 毎日予約の実際の投稿 | daily実配送の確認 | 開発用Discord guild、開発DB、本文ありdaily | 指定時刻まで待つ | 指定時刻に本文が1回だけ投稿された。重複投稿なし | 2026-08-21 | Oto | 実Discord上で目視確認 | テスト用予約は終了状態またはfailed状態で保持し、30日cleanup対象とする |
| [x] 毎週予約の実際の投稿 | weekly実配送の確認 | 開発用Discord guild、開発DB、本文ありweekly | 指定曜日・指定時刻まで待つ | 指定曜日・指定時刻に本文が1回だけ投稿された。重複投稿なし | 2026-08-21 | Oto | 実Discord上で目視確認 | テスト用予約は終了状態またはfailed状態で保持し、30日cleanup対象とする |
| [x] 終了日当日の投稿とended | 終了境界の確認 | 開発用Discord guild、開発DB、終了日当日にrunがある定期予約 | 最後のrunを到来させる | 終了日当日の投稿を実行し、投稿後にScheduleがendedとなった。次回投稿なし | 2026-08-21 | Oto | 実Discord上で目視確認 | テスト用予約は終了状態またはfailed状態で保持し、30日cleanup対象とする |
| [x] Bot停止中の単発15分以内 | 遅延投稿Recoveryの確認 | 開発用Discord guild、開発DB、未来の単発予約 | 予定時刻前にBotを正常停止し、予定時刻から15分以内に再起動 | 起動時Recovery後に本文を1回だけ投稿した。重複投稿なし | 2026-08-21 | Oto | 実Discord上で目視確認 | テスト用予約は終了状態またはfailed状態で保持し、30日cleanup対象とする |
| [x] Bot停止中の単発15分超過 | 期限超過の確認 | 開発用Discord guild、開発DB、未来の単発予約 | 予定時刻前にBotを正常停止し、予定時刻から15分を超えて再起動 | 予約本文を投稿せず、Scheduleがfailedとなり、運営者チャンネルへ失敗通知Embedを表示した。重複投稿なし | 2026-08-21 | Oto | 実Discord上で目視確認 | テスト用予約は終了状態またはfailed状態で保持し、30日cleanup対象とする |
| [x] startup delayed通知 | 遅延通知接続の確認 | 開発用Discord guild、開発DB、15分以内の単発予約 | Botを停止・再起動 | 運営者チャンネルへ「遅延した予約投稿を処理します」Embedを表示した。状態は処理中で、投稿先、元の予定日時、完全なUUIDv7、対応案内を表示し、投稿本文は含めない。通知後に実際の本文を1回だけ投稿した | 2026-08-21 | Oto | 実Discord上で目視確認 | テスト用予約は終了状態またはfailed状態で保持し、30日cleanup対象とする |
| [x] operator channel失敗からoperator DM | fallback確認 | 開発用Discord guild、開発DB | 予約先への投稿とoperator channelへの通知を権限不足で失敗させる | operator DMへ失敗通知Embedが1件届いた。投稿本文を含めず、投稿先、状態、予定日時、予約ID、対応案内を表示した。重複通知なし | 2026-08-21 | Oto | 実DiscordおよびBotターミナルで目視確認 | `#bot-failure-test`と`#一般`のBot送信権限、開発用サーバーからのDM受信設定を復元し、Botの継続稼働を確認 |
| [x] operator DM失敗からlog fallback | 最終fallback確認 | 開発用Discord guild、開発DB | 予約先への投稿とoperator channelへの通知を権限不足で失敗させ、operator DMをサーバーのDM設定で拒否する | Discordへの通知は届かず、Botターミナルに固定ERRORイベント`notification_log_route_terminal`を記録した。投稿本文、token、DATABASE_URL、Discordレスポンス本文、例外全文、tracebackなし。`internal_errors=0`で、fallback後も投稿loopと通知loopが継続し、予約状態はfailed | 2026-08-21 | Oto | 実DiscordおよびBotターミナルで目視確認 | `#bot-failure-test`と`#一般`のBot送信権限、開発用サーバーからのDM受信設定を復元し、Botの継続稼働を確認 |
| [x] PostgreSQLバックアップ | dump手順の確認 | 開発用PostgreSQL、開発DB`discord_bot_dev`、Bot停止状態 | PostgreSQL公式`pg_dump`でリポジトリ外の一時ディレクトリへcustom形式dumpを取得し、サイズ、SHA-256、`pg_restore --list`を確認 | 44,754 bytesのdumpを作成し、SHA-256は`10d9f0cdff2172015addb0f4999a1cc598d2e11c4318f7721b45ec4182b65abc`。`pg_restore --list`で読取成功。開発DBは読み取りのみで、`.env`やパスワードを表示していない | 2026-08-21 | Oto | Codex CLIの検証結果 | 一時dump削除済み。開発用postgresはhealthy、postgres_data Volume維持 |
| [x] 別DBへの復元 | 復元可能性の確認 | 開発用PostgreSQL、開発DB`discord_bot_dev`、新規の空DB`discord_bot_restore_verify_20260821`、Bot停止状態 | custom形式dumpを開発DBとは別の一時DBへ復元し、revision、主要6テーブル件数、構造を読み取り検証 | 復元成功。Alembic currentとrepository headは`8e5b2f1c4a90`でupgrade不要・未実施。件数はschedules 18/18、schedule_runs 23/23、delivery_attempts 11/11、operation_logs 22/22、notification_logs 14/14、notification_attempts 12/12。テーブル、列、UNIQUE、FK、インデックスが一致し、CHECK制約56件はcast表現を除く正規化後定義・制約名・属性で意味的に一致。notification_attemptsのFKはON DELETE RESTRICT、必要な部分インデックス4件、operation_logsのcompleted許可、NotificationLogの6状態、NotificationAttemptの5状態を確認。開発DBの事前・事後でrevision、件数、構造ハッシュ、DML統計が一致し、開発DBへのDML・restoreおよびMigration操作なし | 2026-08-21 | Oto | Codex CLIの検証結果 | 一時復元DBと一時dumpを削除済み。開発用postgresはhealthy、postgres_data Volume維持 |
| [x] 一時エラー時の1分・5分・15分再試行 | retry間隔の確認 | 専用PostgreSQL、固定Clock、Fake Gateway | transientをattempt 1～3で返す | attempt 1は1分後、attempt 2は5分後、attempt 3は15分後に設定され、runはpendingへ戻りScheduleはactiveを維持した。最終失敗通知なし | 2026-08-21 | Oto | 専用PostgreSQL＋固定Clock＋Fake Gatewayによる隔離受入。重点検証29 passed、0 failed、0 skipped。実時間sleep・実Discord API通信なし。Alembic `8e5b2f1c4a90`（head）、差分なし | postgres_testの6業務テーブルが0件であることを確認し、postgres_testを停止・削除 |
| [x] 4回目の最終失敗 | 最大試行境界の確認 | 専用PostgreSQL、固定Clock、Fake Gateway | 4回目のtransientを返す | attempt 4でrunと単発Scheduleがfailedとなり、`next_attempt_at`はNULL。`run_failed`通知は1件で、attempt 5は作成されない | 2026-08-21 | Oto | 専用PostgreSQL＋固定Clock＋Fake Gatewayによる隔離受入。重点検証29 passed、0 failed、0 skipped。実時間sleep・実Discord API通信なし。Alembic `8e5b2f1c4a90`（head）、差分なし | postgres_testの6業務テーブルが0件であることを確認し、postgres_testを停止・削除 |
| [x] Rate Limit時のRetry-Afterによる再試行 | Retry-After優先の確認 | 専用PostgreSQL、固定Clock、Fake Gateway | 未来のUTC `retry_at`を返す | Fake Gatewayで指定した`retry_at`が保存され、通常のretry間隔より優先された。WorkerやGateway独自のsleep・追加送信なし | 2026-08-21 | Oto | 専用PostgreSQL＋固定Clock＋Fake Gatewayによる隔離受入。重点検証29 passed、0 failed、0 skipped。実時間sleep・実Discord API通信なし。Alembic `8e5b2f1c4a90`（head）、差分なし。安全性と再現性のため実DiscordでRate Limitを意図的に発生させていない | postgres_testの6業務テーブルが0件であることを確認し、postgres_testを停止・削除 |
| [x] sending後に結果不明となった場合の安全側処理 | 二重投稿防止の確認 | 専用PostgreSQL、固定Clock、Fake Gateway | sending後の結果不明と結果保存失敗を模擬 | runをpendingへ戻さず、自動再送なし。Gateway呼び出しは1回。結果保存失敗時はprocessing／sendingを維持してlease Recoveryへ委ね、Recovery後はfailedとして終端し再試行しない | 2026-08-21 | Oto | 専用PostgreSQL＋固定Clock＋Fake Gatewayによる隔離受入。重点検証29 passed、0 failed、0 skipped。実時間sleep・実Discord API通信なし。Alembic `8e5b2f1c4a90`（head）、差分なし。安全性と再現性のため実Discordの通信障害を意図的に発生させていない | postgres_testの6業務テーブルが0件であることを確認し、postgres_testを停止・削除 |
| [x] processing中断Recovery | lease復旧の確認 | 専用PostgreSQL、固定Clock、Fake Gateway | claimed/sending中断と不整合、確定処理失敗を模擬 | retry対象はpendingへ戻り、終端対象はSchedule確定へ接続。run、Attempt、Schedule、未来run、OperationLog、NotificationLogのtransaction rollback、不整合時の安全側終端化と通知冪等性を確認 | 2026-08-22 | Oto | 専用PostgreSQL＋固定Clock＋Fake Gatewayによる隔離受入。追加テスト22 passed、0 failed、0 skipped。重点テスト109 passed、0 failed、0 skipped。PostgreSQL込み全pytest 815 passed、0 failed、0 skipped。実時間sleep・実Discord API通信なし。証跡コミット`b77145379ae8c380fc317ee5349da9f638cb0ee8`。安全性と再現性のため実DiscordでRecovery障害を意図的に発生させていない | テストDBの6業務テーブルが0件であることを確認し、postgres_testを停止・削除 |
| [x] 定期欠落回Recovery | 停止中定期回の確認 | 専用PostgreSQL、固定Clock、Fake Gateway | 2つの独立Sessionで定期欠落回Recoveryを同時に実行 | Session間の重複取得を防止し、未commit状態は別Sessionから不可視。未来runとSchedule単位の集約通知は各1件で、version増加制御とrollbackを確認 | 2026-08-22 | Oto | 専用PostgreSQL＋固定Clock＋Fake Gatewayによる隔離受入。追加テスト22 passed、0 failed、0 skipped。重点テスト109 passed、0 failed、0 skipped。PostgreSQL込み全pytest 815 passed、0 failed、0 skipped。実時間sleep・実Discord API通信なし。証跡コミット`b77145379ae8c380fc317ee5349da9f638cb0ee8`。安全性と再現性のため実DiscordでRecovery障害を意図的に発生させていない | テストDBの6業務テーブルが0件であることを確認し、postgres_testを停止・削除 |
| [x] Notification Recovery | 通知lease復旧の確認 | 専用PostgreSQL、固定Clock、Fake Gateway | claimed/sending通知のlease切れと各不整合を模擬 | attempt 1は1分後、attempt 2は5分後にpending、attempt 3はfinal failedとなりfallbackを冪等に1件生成。sending期限切れはunknownで自動再送・fallbackなし。各不整合のunknown化、既存Attempt非変更、SKIP LOCKED、rollbackを確認 | 2026-08-22 | Oto | 専用PostgreSQL＋固定Clock＋Fake Gatewayによる隔離受入。追加テスト22 passed、0 failed、0 skipped。重点テスト109 passed、0 failed、0 skipped。PostgreSQL込み全pytest 815 passed、0 failed、0 skipped。実時間sleep・実Discord API通信なし。証跡コミット`b77145379ae8c380fc317ee5349da9f638cb0ee8`。安全性と再現性のため実DiscordでRecovery障害を意図的に発生させていない | テストDBの6業務テーブルが0件であることを確認し、postgres_testを停止・削除 |
| [x] stale cancel | 不要通知抑止の確認 | 専用PostgreSQL、固定Clock、Fake Gateway、future draft通知 | active化、pause、delete、日時・Run・通知種別・通知時刻の不一致後にdue化 | 送信前にcancelledとなり、Fake Gateway呼び出し、fallback、再claimなし。terminal通知と別worker所有通知を保護 | 2026-08-22 | Oto | 専用PostgreSQL＋固定Clock＋Fake Gatewayによる隔離受入。追加テスト22 passed、0 failed、0 skipped。重点テスト109 passed、0 failed、0 skipped。PostgreSQL込み全pytest 815 passed、0 failed、0 skipped。実時間sleep・実Discord API通信なし。証跡コミット`b77145379ae8c380fc317ee5349da9f638cb0ee8`。安全性と再現性のため実DiscordでRecovery障害や不整合を意図的に発生させていない | テストDBの6業務テーブルが0件であることを確認し、postgres_testを停止・削除 |
| [x] Recovery不整合通知 | 不整合通知の確認 | 専用PostgreSQL、固定Clock、Fake Gateway | Attempt不整合とoutbox生成失敗を安全にfixtureで作る | 状態確定とNotificationLog outbox生成の原子性、同じ論理イベントの通知重複なしを確認。Presenterは固定された安全な内容だけを生成し、投稿本文、内部DB ID、worker ID、例外全文、traceback、token、DATABASE_URL、Discordレスポンス本文を含まない | 2026-08-22 | Oto | 専用PostgreSQL＋固定Clock＋Fake Gatewayによる隔離受入。追加テスト22 passed、0 failed、0 skipped。重点テスト109 passed、0 failed、0 skipped。PostgreSQL込み全pytest 815 passed、0 failed、0 skipped。実時間sleep・実Discord API通信なし。証跡コミット`b77145379ae8c380fc317ee5349da9f638cb0ee8`。安全性と再現性のため実DiscordでRecovery障害や不整合を意図的に発生させていない | テストDBの6業務テーブルが0件であることを確認し、postgres_testを停止・削除 |
| [x] 通知unknown | 通知二重送信防止の確認 | 固定Clock、Fake Gateway、専用PostgreSQL | Notification Gatewayの送信結果不明とsending lease期限切れを模擬 | NotificationLogとNotificationAttemptをunknownへ終端化し、`next_attempt_at`はNULL。次のpoll cycleとlease Recovery後も再claim・再送なし。Fake Gateway呼び出し1回、fallback 0件。通知とログに秘密情報、投稿本文、例外詳細を含めない | 2026-08-22 | Oto | 固定Clock＋Fake Gateway／モック＋専用PostgreSQLによる隔離受入。追加テスト7 passed、0 failed、0 skipped。重点テスト35 passed、0 failed、0 skipped。PostgreSQL込み全pytest 822 passed、0 failed、0 skipped。実時間sleep・実Discord API通信なし。証跡コミット`0cf300d82413aece0355e526babd1395f101d853`。安全性と再現性のため実Discordで障害を意図的に発生させていない | テストDBの6業務テーブルが0件であることを確認し、postgres_testを停止・削除 |
| [x] JST 04:00 cleanup | maintenance時刻と削除の確認 | 固定Clock、モック、専用PostgreSQL | loop設定と起動順を確認し、due終端データでcleanup cycleを直接実行 | timezone-awareなAsia/Tokyo 04:00設定。Bot起動直後はcleanup本体を実行せず、Recovery・Bootstrap成功後にmaintenance loopを開始。1サイクルのClock取得は1回で同一cutoffを全cleanup処理に使用。30日境界、対象外状態、in-flight保持、100件上限を確認 | 2026-08-22 | Oto | 固定Clock＋Fake Gateway／モック＋専用PostgreSQLによる隔離受入。追加テスト7 passed、0 failed、0 skipped。重点テスト35 passed、0 failed、0 skipped。PostgreSQL込み全pytest 822 passed、0 failed、0 skipped。実時間で04:00まで待機せず、実Discord API通信なし。証跡コミット`0cf300d82413aece0355e526babd1395f101d853` | テストDBの6業務テーブルが0件であることを確認し、postgres_testを停止・削除 |
| [x] cleanup失敗中の投稿・通知継続 | loop分離の確認 | 固定Clock、モック、隔離テスト環境 | cleanup cycleで通常例外とCancelledErrorを個別に発生させ、後続cycleを実行 | 通常例外を固定された安全なログへ変換し、例外全文、traceback、DATABASE_URLを含めない。後続cycleで投稿WorkerとNotification Workerを実行し、投稿・通知loopをstop／cancelせず、maintenance loopも次回実行可能。CancelledErrorは再送出 | 2026-08-22 | Oto | 固定Clock＋Fake Gateway／モック＋専用PostgreSQLによる隔離受入。追加テスト7 passed、0 failed、0 skipped。重点テスト35 passed、0 failed、0 skipped。PostgreSQL込み全pytest 822 passed、0 failed、0 skipped。実時間sleep・実Discord API通信なし。証跡コミット`0cf300d82413aece0355e526babd1395f101d853`。安全性と再現性のため実Discordで障害を意図的に発生させていない | 障害モックを解除し、postgres_testを停止・削除 |
| [x] 再接続時のloop二重起動防止 | runtime冪等性の確認 | 固定Clock、モック、隔離Bot環境 | 同時および連続して`on_ready`を呼び、続けてcloseを2回実行 | Startup Recoveryは1回で、投稿、通知、maintenanceの各`loop.start()`も1回。重複Taskなし。close時に3 loopとstartup Taskを回収し、二重closeも安全 | 2026-08-22 | Oto | 固定Clock＋Fake Gateway／モック＋専用PostgreSQLによる隔離受入。追加テスト7 passed、0 failed、0 skipped。重点テスト35 passed、0 failed、0 skipped。PostgreSQL込み全pytest 822 passed、0 failed、0 skipped。実時間sleep・実Discord API通信なし。証跡コミット`0cf300d82413aece0355e526babd1395f101d853`。安全性と再現性のため実Discordで再接続競合を意図的に発生させていない | Bot接続なし。Taskとloopをテスト内で回収 |
| [x] 別環境セットアップ | 再現性の確認 | 新しいWSL2/Linux環境 | READMEを先頭から実施 | PostgreSQL、Migration、テスト、Bot起動が再現 | 2026-08-22 | Oto | 使い捨てworktree＋専用Compose project＋tmpfs PostgreSQLによる隔離受入。基準コミット`da4f52a1d72a1de48ad79d99cbceeef5b6540629`からdetached worktreeと新規CPython 3.14.4仮想環境を作成し、`.[dev]`導入とpip checkに成功。`.env`はpermission 600、Git ignore・非追跡で、非実在Discord設定とダミーtokenのみ使用。専用Compose project`discord_bot_phase1_setup_20260822`のPostgreSQL 18.4をhost port 56432、tmpfs、専用Volumeなしで起動し、health、upgrade head、current／heads `8e5b2f1c4a90 (head)`、Alembic checkを確認。downgrade、stamp、手動DDLなし。通常pytest 579 passed／243 expected skipped、PostgreSQL統合243 passed／0 failed／0 skipped、全pytest 822 passed／0 failed／0 skipped、Bot未接続境界32 passed、Ruff check・format check・git diff check成功、追跡差分なし、終了時6業務テーブル0件。`python -m discord_ai_reminder_bot`は実行せず、package import、設定読込、Engine／Session／Bot構築、tokenの`bot.run()`境界での展開、Schema revision確認まで実施し、実Discordへのログイン・同期・送信なし | 専用postgresをstop・rmし、専用network、detached worktree、一時override、一時ルートを削除。専用Volume作成なし、`down -v`未使用、port 56432解放、専用project資源残存なし。元リポジトリ、開発用postgres、開発DB、postgres_data Volumeは事前・事後で不変 |

確認済み: **57件**

## 3. 未確認項目

未確認項目は実施前に前提と後片付けをレビューし、危険なものは専用テスト環境またはFake Gatewayで行う。

| 状態・項目 | 目的 | 前提 | 操作 | 期待結果 | 実施日 | 実施者 | 証跡 | 後片付け |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [ ] 定期予約のpause保持と同日救済 | 実Discord上のephemeral View・Modal・表示確認 | 検証用guild、未来および当日到来済みの毎日・毎週予約 | pause後に時刻前再開、時刻後の4選択、timeout、本人以外の操作を確認 | 保持run再利用、同日だけの救済、paused中の無送信、統一状態表示、基本時刻への復帰 | 未実施 | 未定 | 自動テストとは別に実Discordで確認する | 検証予約と投稿を削除 |
| [ ] listページング表示 | 全件数・総ページ数と境界ボタンの確認 | 複数ページの予約 | `/post list`で先頭・中間・最終ページを移動 | 件数とページが正しく、前後ボタンのdisabledが境界に一致 | 未実施 | 未定 | 自動テストとは別に実Discordで確認する | なし |
| [ ] list予約種類フィルター | 状態・ページ・詳細との連携確認 | 単発・毎日・毎週と0件になる条件を用意 | `/post list status:一時停止中`から各種類・すべてを選び、ページ移動、詳細、一覧へ戻るを操作 | 状態条件を維持し、種類変更で1ページへ戻り、件数・default・詳細候補が更新され、0件でも種類変更できる | 未実施 | 未定 | 実Discord未確認 | なし |
| [ ] list選択詳細 | 選択・詳細・戻る・認可・timeout確認 | 複数利用者と予約 | 選択、一覧へ戻る、本人以外操作、120秒待機 | 最新詳細を表示し、本人以外を拒否し、timeout後にViewが消える | 未実施 | 未定 | 自動テストとは別に実Discordで確認する | なし |
| [ ] list更新追従 | status、NULL順、ページ補正確認 | pausedを含む予約 | status絞り込み中に予約数を変更して移動 | pausedは既存NULL後方順、消滅ページは末尾へ補正 | 未実施 | 未定 | 自動テストとは別に実Discordで確認する | 検証予約を削除 |
| [ ] 短縮終了日 | daily・weekly・editの入力と表示確認 | 検証用定期予約 | `明日`、`8/30`、完全日付と不正入力を試す | 完全日付で表示・保存され、半角違反と形式不正を個別案内 | 未実施 | 未定 | 自動テストとは別に実Discordで確認する | 検証予約を削除 |
未確認: **5件**

## 4. 判定記録

- Phase 1手動受入判定: **完了**
- 完了条件: 未確認項目を安全な適用環境で確認し、重大な差異が解消され、実施日・実施者・証跡・後片付けが記録されていること。
- PostgreSQL統合テスト結果はこの手動表と分けて記録し、skipを成功扱いにしない。
