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

確認済み: **41件**

## 3. 未確認項目

未確認項目は実施前に前提と後片付けをレビューし、危険なものは専用テスト環境またはFake Gatewayで行う。

| 状態・項目 | 目的 | 前提 | 操作 | 期待結果 | 実施日 | 実施者 | 証跡 | 後片付け |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [ ] transientの1分・5分・15分再試行 | retry間隔の確認 | 専用テストDB、Fake Gateway、固定Clock | transientを連続返す | attempt 2/3/4が1/5/15分後になる | — | — | — | テストDBだけ破棄 |
| [ ] 4回目の最終failed | 最大試行境界の確認 | 専用テストDB、Fake Gateway | 4回transientを返す | run failed、単発Schedule failed、通知生成 | — | — | — | テストDBだけ破棄 |
| [ ] Rate Limit | Retry-After優先の確認 | 専用テストDB、Fake Gateway | 未来のretry_atを返す | 指定時刻を優先し追加送信をしない | — | — | — | テストDBだけ破棄 |
| [ ] sending後unknown | 二重投稿防止の確認 | 専用テストDB、Fake Gateway | sending後に結果不明を模擬 | unknown/failedへ確定し自動再送しない | — | — | — | テストDBだけ破棄 |
| [ ] processing中断Recovery | lease復旧の確認 | 専用テストDB、Fake Gateway | claimed/sending中断を個別に模擬 | claimedは安全ならretry、sendingはunknownで再送なし | — | — | — | テストDBだけ破棄 |
| [ ] 定期欠落回Recovery | 停止中定期回の確認 | 隔離環境、定期予約 | 複数回を過ぎて再起動 | 過去回skipped、未来run 1件、集約通知 | — | — | — | 予約整理 |
| [ ] Notification Recovery | 通知lease復旧の確認 | 専用テストDB、Fake Gateway | claimed/sending通知のlease切れを模擬 | claimedは規則に従いretry、sendingはunknown | — | — | — | テストDBだけ破棄 |
| [ ] 通知unknown | 通知二重送信防止の確認 | Fake Gateway、専用テストDB | 通知sending後の結果不明を模擬 | unknownで終端しretry・fallbackなし | — | — | — | テストDBだけ破棄 |
| [ ] stale cancel | 不要通知抑止の確認 | 専用テストDB、future draft通知 | active化/pause/delete/日時変更後にdue化 | cancelledとなりDiscordを呼ばない | — | — | — | テストDBだけ破棄 |
| [ ] Recovery不整合通知 | 不整合通知の確認 | 専用テストDBのみ | Attempt不整合を安全にfixtureで作る | recovery通知が冪等生成される | — | — | — | テストDBだけ破棄 |
| [ ] JST 04:00 cleanup | maintenance時刻と削除の確認 | 専用テストDB、固定Clock相当の受入fixture | due終端データでcycleを検証 | 04:00境界、30日包含、FK順で削除 | — | — | — | テストDBだけ破棄 |
| [ ] cleanup失敗中の投稿・通知継続 | loop分離の確認 | 専用テスト環境 | cleanup対象だけを失敗させる | incomplete/errorでも投稿・通知loop継続 | — | — | — | 障害fixture解除 |
| [ ] 再接続時のloop二重起動防止 | runtime冪等性の確認 | 隔離Bot環境 | Discord再接続を安全に発生させる | Recoveryと3 loopが二重startしない | — | — | — | Bot正常停止 |
| [ ] バックアップ | dump手順の確認 | 承認済み非本番DB、保存先 | Runbookどおりcustom dump、list、hash | 成功確認でき秘密を残さない | — | — | — | 制限付き別保管または安全に廃棄 |
| [ ] 別DBへの復元 | 復元可能性の確認 | 承認済みバックアップ、別の空DB | Runbookどおりpg_restoreと読取検証 | revision、件数、CHECK/FKが整合 | — | — | — | 復元DBを運用判断後に隔離・廃棄 |
| [ ] 別環境セットアップ | 再現性の確認 | 新しいWSL2/Linux環境 | READMEを先頭から実施 | PostgreSQL、Migration、テスト、Bot起動が再現 | — | — | — | Bot停止、postgresだけ停止 |

未確認: **16件**

## 4. 判定記録

- Phase 1手動受入判定: **未完了**
- 完了条件: 未確認項目を安全な適用環境で確認し、重大な差異が解消され、実施日・実施者・証跡・後片付けが記録されていること。
- PostgreSQL統合テスト結果はこの手動表と分けて記録し、skipを成功扱いにしない。
