# Phase 2 手動受入

実Discordで未確認の項目を記録する。確認時も開発用または承認済み検証guildだけを使用し、秘密情報や投稿本文を証跡へ含めない。

- 実施日: 2026-08-30
- 実施者: Oto
- 集計: 確認済み 28件／未確認 19件（合計47件）

## 実Discord確認記録

show／edit／delete／pause／resumeの`public_id`欄で候補表示とコマンド別の操作可能予約への絞り込みを確認した。候補選択後の`/post show`、完全なcanonical UUIDv7の直接入力、UUID前方一致、種別名・状態名・数値channel ID・channel名による検索を確認した。channel名は完全一致、前方・部分一致、先頭`#`付き、英字のcase-insensitive、日本語で確認した。

候補に本文が表示されず、他の個別テストchannelの予約が混在しないこと、候補が約3秒以内に表示されることを確認した。Botターミナルには`command_error`、traceback、秘密情報を含む異常ログがなかった。複数条件を含む受入項目は、全条件を直接確認できた場合だけ確認済みとした。

予約詳細Viewでは、`/post show`から開いた有効な毎日予約に一時停止と削除が表示され、「一覧へ戻る」は表示されないことを確認した。一時停止後は同じメッセージがpaused詳細、再開と削除、成功通知、「⚠️ 一時停止について」へ更新された。投稿時刻前の再開では次回投稿日時を維持してactive詳細と一時停止へ戻り、成功通知と保持投稿回の案内が表示された。

`/post list`由来の詳細には一時停止、削除、「一覧へ戻る」が表示された。一覧へ戻ると状態・種類フィルター・ページを維持した最新一覧が同じメッセージに表示され、操作を継続できた。

作成者本人の予約では理由入力なしの削除確認が表示され、cancelではDBを更新せず最新詳細へ戻り、状態と操作ボタンを維持した。確定後は同じメッセージがdeleted詳細と成功案内へ更新され、削除操作が無効になった。一覧由来では「一覧へ戻る」が維持され、通常一覧から除外された予約を`/post show`ではdeleted詳細として確認できた。

投稿時刻後の再開では、次回から再開、本日分を今すぐ投稿、本日分の時刻を指定、cancelの4択が表示された。cancelではDBを更新せずpaused詳細と再開・削除へ戻った。再度4択から「次回から再開」を実行すると、基本投稿時刻を変更せず本日分を見送って翌日の基本時刻へ進み、active詳細、成功通知、「⚠️ 再開について」が表示された。

この確認中、Botログに`command_error`、`internal_errors=1`、traceback、`InteractionResponded`、View／Task警告はなかった。管理者による他人の予約削除理由、本日分の即時投稿・時刻指定、各15分timeout、別利用者操作、DM・設定外guild・操作中の権限喪失、`expected_version`競合、Bot終了時のView／Modal回収、Web版・スマホ版など、直接確認していない条件は未確認のままとした。

予約詳細から、単発では投稿先・投稿日時・本文、毎日では投稿先・基本投稿時刻・終了日・本文、毎週では投稿先・曜日・基本投稿時刻・終了日・本文を実Discordで編集した。終了日は完全な`YYYY-MM-DD`で表示され、定期予約の次回投稿は新しい曜日・基本時刻から再計算された。編集後も同じephemeral詳細メッセージが最新の状態別操作ボタンとともに更新され、同じ画面から連続して2回目・3回目の編集Modalを開けた。単発には一時停止が表示されず、毎週には編集・一時停止・削除が表示された。

no-opでは「変更内容がありません。」と表示され、表示内容と予約状態は変化しなかった。本文と終了日を空欄にして解除でき、本文解除後はdraft／🟡 下書きになった。Interactionの時間切れや誤った予約不存在・編集不可案内は発生せず、Botターミナルに`ERROR`、traceback、`schedule_detail_edit_modal_error`などの異常ログはなかった。権限競合、別利用者、別guild、timeout、Bot再起動、削除・pause・resumeは今回の編集確認の対象外とした。

### 2026-08-30 追加確認

PC版の`/post show`で`public_id`のAutocomplete候補を表示し、候補から予約を選択して詳細を表示できた。削除済み予約も削除済み状態として詳細表示できた。

Autocompleteは空入力とchannel名検索で概ね3秒以内に候補が表示され、`#`だけの入力では安全な空候補になった。一度だけ`/post resume`で「オプション読み込み失敗」が表示されたが、再試行では候補が表示され、正常に実行できた。この一時失敗を踏まえ、完全な応答安定性を要求する「応答時間」は未確認を維持した。また、改行、制御文字、ゼロ幅文字、101文字入力は確認していないため、「入力拒否」も未確認を維持した。

`/post show`、`/post edit`、`/post pause`、`/post resume`、`/post delete`のすべてで、UUIDを手入力せずAutocomplete候補から予約を選択して実行できた。一時停止後はpaused予約がresume候補に表示され、再開できた。deleteでは検証用の使い捨て単発予約だけを削除し、削除後は削除済みとして記録された。

`/post list`と`/post show`の表示後、16分以上経過しても部品は無効化されなかった。16分後も一覧のフィルターとページ操作が動作し、詳細の編集Modalを開けた。`schedule_list_timeout_response_failed`は発生しなかった。旧実装では`schedule_detail_timeout_response_failed`が一度発生したが、仕様変更後の最新実装では15分timeout経路が廃止され、今回の長寿命View確認では発生しなかった。Bot再起動後は、新しい`/post list`または`/post show`を実行する案内を確認した。一方、最新認可・状態・version・run・attemptの再検証までは今回の証跡だけで全条件を満たさないため、「一覧・詳細View長寿命化」は未確認を維持した。

詳細画面から編集Modalを開いて右上の×で閉じた後、同じ親詳細画面から2回目、3回目の編集Modalを開けた。3回目は変更なしで送信し、正常なno-op案内を確認した。「指定された予約は見つからないか、編集できません。」と「時間内に応答しませんでした」は表示されず、異常ログもなかった。二重submit防止とModal自体の15分timeoutは確認していないため、「詳細編集Modal lifecycle」は未確認を維持した。「詳細編集no-op／競合」も競合条件を確認していないため未確認を維持した。

`/post edit`で`public_id`だけを候補から選択した場合は「変更する項目を1つ以上指定してください。画面を見ながら編集する場合は /post show を使用してください。」と表示された。現在と同じ値を明示指定した場合は「実際に変更される項目がありません。」と表示され、予約内容、状態、次回投稿は変更されず、異常ログもなかった。OperationLogが生成されないことまでは今回確認していないため、「`/post edit` 変更指定なし」は未確認を維持した。`/post edit`は指定項目を直接変更する短縮・上級者向け経路、詳細画面の✏️編集は画面を確認しながら編集する一般利用者向け経路として確認した。

スマホ、別利用者、別guild、管理者境界、権限喪失、競合、Fake Gateway、および詳細由来の一時停止・再開4択など、今回直接確認していない項目は変更していない。

### 2026-08-30 自動テストによる隔離受入

- 実施者: Oto
- 証跡commit: `a751fc8db7287d398b9518840bd6ec0cc2dc73fe`
- 証跡種別: 自動テストによる隔離受入
- 実行結果: 専用PostgreSQL込み全pytest 1028件成功（通常pytest 746件成功／282件skip、重点テスト31件成功）

「最大件数」は`tests/integration/test_schedule_queries_integration.py::test_autocomplete_owner_admin_guild_deleted_limit_and_stable_order`で、専用PostgreSQLに27件を作成し、`next_run_at`と内部順序による安定順および先頭25件への制限を直接確認した。

「channel cache miss」は`tests/test_post_commands.py::test_autocomplete_uses_cache_only_and_cache_failure_is_safe`で、channel cache取得失敗時に空候補となりREST取得も予約検索も行わないことを確認した。`tests/test_post_presenter.py::test_autocomplete_choice_uses_safe_channel_id_fallback`で、別条件から得た候補のchannel名が安全に解決できない場合に短縮channel IDを表示することを確認した。

「詳細編集ボタン」は`tests/test_schedule_queries.py::test_detail_action_basic_state_and_type_matrix`、`tests/test_schedule_queries.py::test_detail_actions_fail_closed_for_run_and_attempt_conflicts`、`tests/test_post_commands.py::test_detail_action_buttons_follow_read_only_availability`、`tests/test_post_commands.py::test_direct_show_builds_detail_context_and_delete_button`で、編集可否の状態・種別・run・attempt境界と、先頭位置での有効／disabled表示を直接確認した。

「詳細編集後の一覧復帰」は`tests/test_post_commands.py::test_detail_edit_modal_submits_multiple_fields_and_clear_flags_atomically`、`tests/test_post_commands.py::test_second_edit_opens_modal_and_no_op_refreshes_same_message`、`tests/test_post_commands.py::test_detail_back_preserves_filters_page_and_clamps_latest_list`で、編集後の最新詳細が一覧由来Contextと最新操作可否を維持し、戻る際に同じフィルターとページを使って最新一覧を取得して有効ページへclampすることを確認した。

「`/post edit` 変更指定なし」は`tests/test_post_commands.py::test_edit_public_id_only_uses_dedicated_safe_response_without_session`と`tests/test_post_commands.py::test_edit_autocomplete_selected_public_id_only_uses_same_dedicated_response`で、直接入力とAutocomplete候補選択の両方について専用ephemeral案内、`AllowedMentions.none()`、Service・Session・transaction未生成を確認した。DB接続経路へ入らないため、DB更新とOperationLog生成も行われない。

「詳細中の一覧更新」は、件数減少時の再取得と末尾ページclampは直接確認できるが、詳細表示中に予約状態が変化した場合の最新一覧行を直接assertする既存nodeがないため未確認を維持した。「一覧・詳細View長寿命化」はListの実ViewStoreによる900秒相当dispatch、Detailのtimeoutなし・disabledなし・DB資源非保持、操作時の各安全境界を個別に確認できる一方、Detailを実ViewStoreから900秒相当後にdispatchすることとBot再起動後に古い画面を復元しないことを直接assertしていないため未確認を維持した。

### 2026-08-30 View隔離重点受入

- 実施者: Oto
- 証跡種別: Fake Interaction／実ViewStore／固定Clock／専用PostgreSQLによる隔離受入
- 実行結果: 追加テスト7件成功、対象重点テスト56件成功、通常pytest 752件成功／283件skip、専用PostgreSQL込み全pytest 1035件成功

「詳細中の一覧更新」は`tests/test_post_commands.py::test_detail_back_uses_changed_service_snapshot_and_recomputes_filtered_page`、`tests/test_post_commands.py::test_detail_back_excludes_changed_status_and_clamps_recomputed_page`、`tests/integration/test_schedule_queries_integration.py::test_detail_return_observes_separate_session_change_and_clamps_filter_page`で、一覧から詳細への遷移、別Sessionによる状態・本文・version変更、最新詳細と一覧の再取得、一覧行の更新、status／type／page維持、filter対象外化後の件数再計算と末尾page clamp、待機中のDB資源非保持を確認した。

「一覧・詳細View長寿命化」は`tests/test_post_commands.py::test_list_and_detail_real_view_store_dispatch_without_timeout_and_close_cleanly`、`tests/test_interactions.py::test_long_lived_ephemeral_view_uses_zero_registration_bridge_and_restores_none`、`tests/test_post_commands.py::test_list_view_rejects_other_user_and_remains_enabled_without_timeout`、`tests/test_post_commands.py::test_detail_rejects_other_user_dm_wrong_guild_and_permission_loss`、`tests/test_bot_runtime.py::test_setup_hook_does_not_restore_dynamic_schedule_views`で、実ViewStoreによるList／Detail dispatch、`timeout=None`、timeout Taskなし、部品の非disabled、timeout応答・失敗ログなし、操作時の再認可、再実行案内、二重closeを含む回収、再起動時の非復元を確認した。最新状態・version・run・attemptの再検証は状態操作と競合の既存Service／統合nodeを組み合わせて確認した。

「状態別詳細ボタン」は`tests/integration/test_schedule_queries_integration.py::test_detail_action_availability_for_every_valid_state_and_type`、`tests/integration/test_schedule_queries_integration.py::test_detail_action_run_attempt_and_time_boundaries`、`tests/integration/test_schedule_queries_integration.py::test_detail_paused_resume_requires_pristine_pending`、`tests/test_post_commands.py::test_detail_action_buttons_follow_read_only_availability`、`tests/test_post_commands.py::test_detail_custom_id_is_fixed_and_close_collects_view`で、全有効状態・種別、processing run、claimed／sending／unknown attempt、current run不整合、paused保持run不整合に対する安全側の操作可否とUI、fixed custom_id、情報境界を確認した。操作後のService再検証は既存のversion・状態競合nodeで確認した。

「再開cancel／timeout」は`tests/test_post_commands.py::test_detail_resume_cancel_timeout_and_races_are_read_only_and_recoverable`と`tests/test_post_commands.py::test_close_collects_open_resume_modal_and_parent_view`で、長寿命の最新親Detailへの復帰、cancel／timeout時のDB経路未開始とpaused snapshot維持、有限timeout、timeout後のdisabledと再取得案内、二重cancel／timeout競合、Bot close時のView・Modal・wait回収を確認した。Sessionを開始しないためrun、version、OperationLog、NotificationLogを変更せず、実Gateway通信も行っていない。

「削除cancel／timeout」は`tests/test_post_commands.py::test_detail_delete_cancel_timeout_and_races_are_read_only_and_recoverable`、`tests/test_post_commands.py::test_close_stops_and_collects_delete_views`、`tests/test_post_commands.py::test_delete_view_rejects_other_user_and_double_confirmation`で、長寿命の最新親Detailへの復帰、cancel／timeout時のDB経路未開始とSchedule snapshot維持、有限timeout、timeout後のdisabled、二重confirm／cancel／timeout競合、Bot close時のView・wait回収を確認した。Sessionを開始しないため削除、run更新、OperationLog、NotificationLog生成を行わず、実Gateway通信も行っていない。

「直接show詳細基盤」は今回の隔離重点検証として、`tests/test_post_commands.py::test_direct_show_builds_detail_context_and_delete_button`、`tests/integration/test_schedule_queries_integration.py::test_detail_action_availability_for_every_valid_state_and_type`、`tests/test_post_commands.py::test_detail_action_buttons_follow_read_only_availability`で、共通`ScheduleDetailView`、canonical public_id、最新expected_version、actor ID、状態別部品、直接showでの「一覧へ戻る」非表示を確認した。`tests/test_post_commands.py::test_detail_rejects_other_user_dm_wrong_guild_and_permission_loss`、`tests/test_post_commands.py::test_detail_back_rechecks_schedule_ownership_and_prevents_double_action`、`tests/test_schedule_queries.py::test_show_uses_guild_public_id_and_enforces_owner`で、Contextへguild IDを保持せず、各操作のInteractionから現在guildを検証し、DM、別guild、権限喪失、所有者／管理者境界を安全な固定案内で再検証することを確認した。`tests/test_post_commands.py::test_detail_custom_id_is_fixed_and_close_collects_view`、`tests/test_post_presenter.py::test_detail_never_displays_internal_version`、`tests/test_interactions.py::test_initial_and_followup_responses_are_ephemeral_with_mentions_disabled`で、内部DB IDをBot層へ渡さないDTO境界、custom_idとEmbedの情報境界、ephemeral、`AllowedMentions.none()`を確認した。証跡commitは未コミットのテスト変更を含むため記録せず、実施日2026-08-30、実施者Oto、証跡種別はFake Interaction／実ViewStore／固定Clock／専用PostgreSQLによる自動テスト隔離受入とする。

| 状態 | 確認項目 | 期待結果 |
|---|---|---|
| [x] | PC版 `/post show` | 閲覧可能な候補が3秒以内に表示され、deletedは削除済み表示になる |
| [x] | Web版 `/post edit` | 編集可能な候補だけが表示される |
| [x] | `/post edit` 変更指定なし | Autocomplete候補選択後を含め、予約IDだけでは専用案内をephemeral表示し、DB更新・OperationLog生成を行わない |
| [ ] | スマホ版 `/post delete` | 100文字以内の候補名が識別でき、本文を含まない |
| [x] | PC／Web `/post pause` | activeのdaily／weeklyだけが表示される |
| [x] | PC／Web `/post resume` | 再開可能なpaused daily／weeklyだけが表示される |
| [x] | 最大件数 | 候補が最大25件で安定順に表示される |
| [x] | channel名完全一致 | `tester-a`で`#tester-a`への予約が表示される |
| [x] | channel名前方・部分一致 | `tester`で`#tester-a`、`#tester-b`、`お知らせ`で`#運営お知らせ`等が表示される |
| [x] | `#`・casefold・日本語 | `#一般`、英字の大文字入力、日本語channel名で同じ安全な検索結果になる |
| [ ] | channel境界 | 閲覧不能、別guild、Thread、Category、Voice、DMの名前は候補検索に使われない |
| [ ] | 入力拒否 | `#`だけ、制御文字、改行、ゼロ幅文字、100文字超は名前検索されず安全に空候補になる |
| [x] | 数値ID互換 | 数値channel ID完全一致でも従来どおり検索できる |
| [ ] | 本人境界 | 許可ロール利用者には本人所有の予約だけが表示される |
| [ ] | 管理者境界 | 管理者には設定guild内の他作成者の予約も表示される |
| [x] | 候補選択 | 選択後に完全なUUIDv7が入り、各コマンドを実行できる |
| [x] | 直接入力互換 | 候補を選ばず完全なcanonical UUIDv7を貼り付けて実行できる |
| [ ] | 状態競合 | 候補表示後、実行前に状態・runを変えると既存の安全な拒否になる |
| [ ] | 認可失敗 | DM、設定外guild、権限なしでは通常メッセージを送らず空候補になる |
| [ ] | 情報境界 | 本文、秘密情報、内部ID、他guild情報が候補・利用者応答・通常ログに出ない |
| [x] | channel cache miss | 名前検索は該当なしとなり、REST取得せず、別条件の候補表示では短縮channel IDを使う |
| [ ] | 応答時間 | 空入力・検索入力のどちらも3秒以内に候補または空候補が返る |
| [x] | 直接show詳細基盤 | `/post show`から共通`ScheduleDetailView`を表示する。長寿命Contextにはcanonical public_id、最新expected_version、actor ID、必要なlist originだけを保持し、guild IDや内部DB IDは保持・伝達しない。編集可能状態は✏️編集、activeのdaily／weeklyは⏸️一時停止、pausedのdaily／weeklyは▶️再開、削除可能状態は🗑️削除を表示し、直接show由来では「一覧へ戻る」を表示しない。操作不能なボタンは現在の仕様に従ってdisabled表示または非表示とし、version、内部DB ID、可否理由をEmbedへ表示しない。応答はephemeralかつ`AllowedMentions.none()`とする。各ボタン操作時にInteractionから現在guildを取得し、DMを拒否して、Interactionと最新Scheduleのguild境界、所有者／管理者認可、最新状態を再検証する。別guildや権限喪失時は安全な固定案内とし、fixed custom_idへguild ID、user ID、public_id、versionを含めない |
| [x] | 一覧詳細から戻る | 一覧で予約を選択後、同じephemeralメッセージの「一覧へ戻る」で元の状態・種類・ページ条件へ戻れる |
| [x] | 詳細中の一覧更新 | 詳細表示中に予約件数や状態が変わっても、戻ると最新一覧を取得し、消滅した末尾ページは最後の有効ページへ補正される |
| [x] | 一覧・詳細View長寿命化 | 15分経過後もList／DetailのButton・Selectが有効でcallbackへdispatchされ、最新認可・状態・version・run・attemptを再検証する。Bot再起動後の古い画面は復元せず、`/post list`／`/post show`再実行で復帰する |
| [ ] | 詳細情報境界 | version、内部DB ID、操作可否の内部理由、秘密情報が詳細Embed・応答・通常ログに表示されない |
| [x] | 状態別詳細ボタン | 現在のSchedule状態、種別、run、attemptから操作可否をread-onlyで決定し、edit、pause、resume、deleteを状態マトリクスに従って表示する。processing、claimed、sending、unknown、不整合では安全側で操作不可とし、操作時に最新状態と認可を再検証する。fixed custom_idを使用し、予約IDやversion等を含めない |
| [ ] | 詳細から一時停止 | `/post show`と一覧詳細の両方で一時停止でき、成功通知、paused詳細、注意事項へ更新される |
| [x] | 詳細から即時再開 | 保持投稿時刻前に再開でき、成功通知と最新active／draft詳細へ更新される |
| [ ] | 詳細から再開4択 | 保持投稿時刻後に4択が表示され、各選択肢が既存再開規則どおり動作する |
| [x] | 再開cancel／timeout | 親`ScheduleDetailView`はBot稼働中操作可能とし、有限timeoutの`ResumeChoice`はcancelでDB更新せずpausedを維持して最新Detail Viewへ戻る。timeoutでもDB更新せずpausedを維持し、ResumeChoiceを操作不能にして親詳細を再取得する方法を案内する。待機中にSession、transaction、row lockを保持せず、Bot close時にViewとwait Taskを回収する |
| [x] | 詳細から作成者削除 | 作成者は理由入力なしで確認へ進み、成功後にdeleted詳細と固定通知が表示される |
| [ ] | 管理者の他人削除理由 | 管理者が他人の予約を削除すると1～500文字の理由Modalが先に表示され、空白だけを拒否する |
| [x] | 削除cancel／timeout | 親`ScheduleDetailView`はBot稼働中操作可能とし、有限timeoutの`DeleteConfirm`はcancelでDB更新・削除・OperationLog生成を行わず最新Detail Viewへ戻る。timeoutでは削除せず、待機中にSession、transaction、row lockを保持しない。Bot close時にViewとwait Taskを回収する |
| [ ] | 詳細操作競合 | 古い詳細からの操作が固定の競合案内で拒否され、最新詳細とボタンへ更新される |
| [ ] | 詳細操作情報境界 | custom_id、Embed、応答、通常ログに予約本文、version、内部ID、理由、秘密情報、例外全文が出ない |
| [x] | 詳細編集ボタン | 編集可能時だけ有効で、操作不能時は先頭位置にdisabled表示される |
| [x] | 単発編集Modal | 投稿先、完全なJST投稿日時、本文の現在値が表示され、一度に編集できる |
| [x] | 毎日編集Modal | 投稿先、投稿時刻、終了日、本文の現在値が表示され、一度に編集できる |
| [x] | 毎週編集Modal | 投稿先、曜日、投稿時刻、終了日、本文の現在値が表示され、一度に編集できる |
| [x] | 詳細編集clear | 空欄で本文削除と終了日解除ができ、状態・run規則が維持される |
| [ ] | 詳細編集no-op／競合 | no-opは更新せず固定案内、古いModalは競合案内と最新詳細へ戻る |
| [ ] | 詳細編集認可・channel境界 | 本人・管理者境界と同guild TextChannel・Bot権限をsubmit時に再確認する |
| [ ] | 詳細編集Modal lifecycle | 二重submitを防止し、close／15分timeout後も親詳細から再度編集できる |
| [x] | 詳細編集後の一覧復帰 | 最新詳細の操作と一覧由来Contextを維持し、戻ると最新一覧へclampされる |
| [ ] | 詳細編集表示・情報境界 | 本文全文や内部情報を通知・custom_id・通常ログへ出さず安全に表示される |
