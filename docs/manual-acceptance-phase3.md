# Phase 3 受入

Phase 3の受入をPhase 1・Phase 2から分離して記録する。第1段階の`/post show` Autocomplete改善と、第2段階2AのProviderを使わない予約名基盤を対象とする。AI生成、費用制御、Provider、Jobは収録しない。自動テストで確認できる項目と実Discord確認が必要な項目を分け、実Discordの証跡がない項目は確認済みにしない。

- 実施日: 2026-08-30
- 実施者: Codex（自動テスト）
- 証跡: 重点テスト322件、通常pytest 815件成功／297件skip、専用PostgreSQL込み全pytest 1112件成功
- 2A証跡: 基盤重点テスト372件、Modal dispatch・ViewStore・競合重点テスト44件、残る認可境界6 node・17ケース、通常pytest 859件成功／324件skip、専用PostgreSQL込み全pytest 1183件成功。Migration upgrade／downgrade／upgrade、既存行backfill、downgrade guard、Alembic current／heads／check成功
- 集計: 確認済み 40件／未確認 0件（合計40件）
- Phase 3第1段階受入判定: 完了
- Phase 3第2段階2A受入判定: 完了

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
