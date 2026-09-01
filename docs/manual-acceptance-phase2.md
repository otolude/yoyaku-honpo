# Phase 2 手動受入

実Discordで未確認の項目を記録する。確認時も開発用または承認済み検証guildだけを使用し、秘密情報や投稿本文を証跡へ含めない。

- 実施日: 2026-08-30
- 実施者: Oto
- 集計: 確認済み 47件／未確認 0件（合計47件）
- Phase 2受入判定: 完了

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
- 証跡対象: identity書換え前の非公開対応commit
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

### 2026-08-30 Autocomplete境界隔離受入

- 実施者: Oto
- 既存証跡種別: 既存自動テストによる隔離受入
- 既存証跡対象: identity書換え前の非公開対応commit
- 追加証跡種別: Fake Interaction／固定Clock／専用PostgreSQLによる隔離受入

「本人境界」「管理者境界」は`tests/integration/test_schedule_queries_integration.py::test_autocomplete_owner_admin_guild_deleted_limit_and_stable_order`と`tests/test_schedule_queries.py::test_autocomplete_scopes_creator_and_returns_immutable_projection`で、本人だけへのcreator絞り込み、管理者のguild内全作成者参照、別guild除外、不要情報を持たないimmutable DTOを確認した。「認可失敗」は`tests/test_interactions.py::test_unsafe_or_unauthorized_interaction_is_rejected`と`tests/test_interactions.py::test_tree_denies_autocomplete_with_only_empty_choices`で、DM、設定外guild、非member、許可ロールなしを拒否し、Autocompleteには通常メッセージを送らず空候補だけを返すことを確認した。

「詳細編集no-op／競合」は`tests/test_post_commands.py::test_daily_detail_edit_complete_noop_uses_no_changes_response_not_input_error`、`tests/test_post_commands.py::test_detail_edit_expected_version_conflict_still_refreshes_detail`、`tests/integration/test_schedule_editing_integration.py::test_edit_expected_version_conflict_changes_nothing`、`tests/integration/test_schedule_editing_integration.py::test_consecutive_edits_use_latest_version_and_noop_does_not_increment`で、no-op時の固定案内・更新なしと、古いexpected versionの競合案内・最新詳細復帰・DBとOperationLog非変更を確認した。「詳細編集Modal lifecycle」は`tests/test_post_commands.py::test_closed_edit_modal_can_be_reopened_without_retiring_parent`、`tests/test_post_commands.py::test_edit_modal_timeout_preserves_parent_detail`、`tests/test_post_commands.py::test_second_edit_opens_modal_and_no_op_refreshes_same_message`で、旧Modal停止、遅延submitと二重submitの抑止、×相当の無通知close後と有限timeout後の親詳細維持・再オープンを確認した。

「channel境界」は`tests/test_post_commands.py::test_autocomplete_resolves_visible_cached_text_channel_names`と`tests/test_post_commands.py::test_autocomplete_uses_cache_only_and_cache_failure_is_safe`で、同guildかつ閲覧可能なTextChannelだけをID集合へ入れ、閲覧不能、別guild、Thread、Category、Voice、DM、cache失敗を除外し、RESTを呼ばず、失敗詳細を応答・通常ログへ出さないことを確認した。「入力拒否」は`tests/test_post_commands.py::test_autocomplete_rejects_unsafe_channel_search_without_query`と`tests/test_schedule_queries.py::test_autocomplete_invalid_search_returns_empty_without_opening_session`で、`#`だけ、trim後空、改行、NUL、その他の制御文字、U+200B、101文字をQuery／Session開始前に空候補とし、通常応答・例外ログを生成しないことを確認した。既存の`tests/test_post_commands.py::test_autocomplete_resolves_visible_cached_text_channel_names`と`tests/test_schedule_queries.py::test_autocomplete_accepts_only_fixed_searches`で安全な日本語・英字・先頭`#`検索を維持する。

「状態競合」は`tests/integration/test_schedule_queries_integration.py::test_autocomplete_selection_is_revalidated_after_separate_session_state_change`で、edit／pause／resume／deleteの候補取得後に別Sessionで状態とversionを変更し、選択済みcanonical UUIDをFake Interactionで実行して、最新DB状態による固定拒否、DB・OperationLog非変更を確認した。「情報境界」は`tests/integration/test_schedule_queries_integration.py::test_autocomplete_dto_and_choices_exclude_body_internal_id_and_other_guild_canaries`、`tests/integration/test_schedule_queries_integration.py::test_autocomplete_owner_admin_guild_deleted_limit_and_stable_order`、`tests/test_post_commands.py::test_autocomplete_returns_full_uuid_and_admin_scope`、`tests/test_post_commands.py::test_autocomplete_failure_is_empty_and_logs_only_fixed_event`、`tests/test_post_commands.py::test_autocomplete_presenter_failure_is_empty_and_logs_only_fixed_event`、`tests/test_post_presenter.py::test_autocomplete_choice_is_bounded_safe_and_omits_paused_datetime`で、最大25件・100文字、canonical public UUID value、本文・秘密・内部ID・別guild情報を持たないDTO／候補／応答／固定イベントログ境界を確認した。

### 2026-08-30 詳細状態操作群隔離受入

- 実施者: Oto
- 証跡種別: Fake Interaction／固定Clock／実ViewStore／専用PostgreSQLによる隔離受入

「詳細から一時停止」は`tests/test_post_commands.py::test_detail_pause_callback_refreshes_paused_detail_and_preserves_origin`で、直接show／一覧由来Contextから固定custom_idのcallbackを通し、expected_version付きService呼出しを1回に直列化して、固定成功通知、最新paused詳細、再開・削除、一覧origin、注意事項、`AllowedMentions.none()`、長寿命親Viewへの所有権移譲を確認した。`tests/integration/test_schedule_pause_integration.py::test_pause_preserves_future_initial_and_skips_retry`、`tests/integration/test_schedule_pause_integration.py::test_pause_skips_due_initial_run`、`tests/integration/test_schedule_pause_integration.py::test_processing_claimed_and_sending_reject_pause`、`tests/integration/test_schedule_pause_integration.py::test_double_pause_and_double_resume_add_no_extra_log`、`tests/integration/test_schedule_pause_integration.py::test_transaction_rollback_restores_pause`で、daily／weeklyの未来pristine run保持、retry・到来済みrun・DeliveryAttempt境界、ログ、二重操作、lock・rollback規則を専用PostgreSQLで確認した。

「詳細から再開4択」は`tests/test_post_commands.py::test_detail_overdue_resume_choices_use_expected_version_and_refresh_owner`、`tests/test_post_commands.py::test_resume_time_modal_enforces_five_minutes_and_midnight_boundary`、`tests/test_post_commands.py::test_closed_resume_time_modal_can_be_reopened_without_retiring_parent`、`tests/test_post_commands.py::test_detail_resume_cancel_timeout_and_races_are_read_only_and_recoverable`で、Detailから有限timeoutのResumeChoiceへの所有権移譲、3確定modeとcancel、expected_version、操作時認可、時刻Modal、5分境界、23時台境界、×相当後の再オープン、最新Detail復帰、DB資源非保持を確認した。`tests/integration/test_schedule_pause_integration.py::test_overdue_resume_next_regular_skips_hold_and_creates_regular`、`tests/integration/test_schedule_pause_integration.py::test_same_day_overdue_resume_creates_exception_run`、`tests/integration/test_schedule_pause_integration.py::test_resume_without_occurrence_ends_only_contentful`、`tests/integration/test_schedule_pause_integration.py::test_concurrent_resume_creates_one_run`、`tests/integration/test_schedule_pause_integration.py::test_transaction_rollback_restores_resume`で、保持run見送り、通常runと今回限りの例外run、救済不可・終了日、基本時刻・曜日・終了日不変、重複防止、OperationLog／NotificationLog規則を確認した。`tests/test_post_presenter.py::test_resume_missed_occurrence_guidance_is_last`で各modeの最新表示を確認した。

「詳細操作競合」は`tests/test_post_commands.py::test_detail_pause_resume_delete_conflicts_refresh_latest_safe_views`で、pause／resume／deleteの古いexpected_versionをBot callbackから渡し、固定競合案内とcommit後の最新状態に合うViewへ更新する接続を確認した。`tests/integration/test_schedule_pause_integration.py::test_pause_expected_version_conflict_changes_nothing`、`tests/integration/test_schedule_deletion_integration.py::test_delete_expected_version_conflict_changes_nothing`、`tests/integration/test_schedule_pause_integration.py::test_processing_claimed_and_sending_reject_pause`、`tests/integration/test_schedule_deletion_integration.py::test_real_postgres_processing_claimed_or_sending_is_unchanged`、`tests/integration/test_schedule_pause_integration.py::test_concurrent_resume_creates_one_run`、pause／resume／deleteのrollback node群で、snapshot取得後とlock後の再検証、状態・run・attempt競合、二重・別transaction競合、DB・run・ログ非重複を確認した。`tests/test_post_commands.py::test_detail_rejects_other_user_dm_wrong_guild_and_permission_loss`とServiceのowner／guild境界nodeで、別利用者・別guild・権限喪失も固定案内で拒否する。

「詳細操作情報境界」は`tests/test_post_commands.py::test_detail_state_operation_failures_expose_only_fixed_boundary`と`tests/test_post_commands.py::test_detail_state_refresh_presenter_failure_logs_only_fixed_event`でpause／resume／deleteのDB失敗と共通Presenter失敗にcanaryを与え、固定応答、固定イベント名、例外全文・本文・理由・token・DATABASE_URL・worker情報の通常ログ非表示、`AllowedMentions.none()`を確認した。`tests/test_post_commands.py::test_detail_custom_id_is_fixed_and_close_collects_view`、`tests/test_post_commands.py::test_detail_pause_callback_refreshes_paused_detail_and_preserves_origin`、`tests/test_post_commands.py::test_detail_overdue_resume_choices_use_expected_version_and_refresh_owner`、`tests/test_post_commands.py::test_list_and_detail_real_view_store_dispatch_without_timeout_and_close_cleanly`、`tests/test_post_presenter.py::test_detail_never_displays_internal_version`、`tests/test_post_presenter.py::test_show_two_thousand_markup_characters_stays_within_limits`、`tests/test_interactions.py::test_initial_and_followup_responses_are_ephemeral_with_mentions_disabled`で、fixed custom_id、DTO、本文・内部ID・version・reason code・別guild情報、Embed上限、ephemeral、実ViewStore境界を確認した。

### 2026-08-30 詳細編集・情報境界群隔離受入

- 実施者: Oto
- 証跡種別: Fake Interaction／固定Clock／専用PostgreSQLによる隔離受入

「詳細情報境界」は`tests/test_post_commands.py::test_detail_show_list_and_failure_paths_keep_content_and_internal_boundaries`で、共通ScheduleDetailを`/post show`成功と一覧選択から表示し、正式な本文欄、canonical UUIDv7、fixed custom_id、内部version・reason code・秘密の非表示、Embed上限、ephemeral、`AllowedMentions.none()`を確認した。同nodeのQuery／Presenter失敗と`tests/test_post_commands.py::test_detail_state_refresh_presenter_failure_logs_only_fixed_event`で、固定応答・固定イベント名だけを残して例外全文、token、DATABASE_URL、Discord応答sentinelを出さないことを確認した。`tests/integration/test_schedule_queries_integration.py::test_detail_creator_admin_guild_and_detached_dto_boundaries`、`tests/test_schedule_queries.py::test_show_uses_guild_public_id_and_enforces_owner`、`tests/test_post_presenter.py::test_show_preserves_line_breaks_but_escapes_user_markup_and_mentions`、`tests/test_post_presenter.py::test_show_two_thousand_markup_characters_stays_within_limits`で、別guild除外、内部DB ID・worker・秘密を持たないdetached DTO、Markdown・mention安全化、Discord文字数上限を確認した。

「詳細編集認可・channel境界」は`tests/integration/test_schedule_queries_integration.py::test_detail_edit_modal_submit_rechecks_actor_and_commits_latest_detail`で、所有者本人と同guild管理者による他作成者予約のModal submitをFake Interaction・固定Clock・専用PostgreSQLの一続きの経路で実行し、Service内認可、expected_version、commit後の最新Detail、一覧origin、OperationLog、cache-only channel検証を確認した。`tests/test_post_commands.py::test_detail_edit_submit_rechecks_actor_and_administrator_boundary`で、他利用者、DM、設定外guild、ロール喪失、Modal表示後の管理者権限喪失をsubmit時に再検証し、固定案内と親View維持を確認した。`tests/test_post_commands.py::test_detail_edit_channel_failures_use_destination_message_before_transaction`、`tests/test_post_commands.py::test_detail_edit_component_or_channel_id_corruption_is_internal_error`、`tests/test_post_commands.py::test_detail_edit_channel_missing_id_is_internal_error`、`tests/test_post_commands.py::test_detail_edit_multiple_channels_is_input_error_before_transaction`、`tests/test_post_commands.py::test_detail_edit_channel_optional_empty_keeps_current_channel`で、cache miss、別guild、Thread、Category、Voice、DM、閲覧・送信権限、Bot member、不正ID・型・範囲・欠落、未選択を横断し、RESTなし、Session・transaction開始前の固定拒否を確認した。`tests/integration/test_schedule_editing_integration.py::test_edit_expected_version_conflict_changes_nothing`、`tests/integration/test_schedule_editing_integration.py::test_noop_invalid_options_boundary_and_authorization_change_nothing`、`tests/integration/test_schedule_editing_integration.py::test_processing_claimed_and_sending_are_rejected`で、version・状態・run・attemptのlock後再検証とDB・OperationLog非変更を確認した。

「詳細編集表示・情報境界」は`tests/test_post_commands.py::test_detail_edit_result_keeps_body_only_in_modal_and_latest_detail`で、現在本文をModal初期値と最新Detailの正式な本文欄だけへ表示し、成功通知、no-op、競合、DB失敗の案内・custom_id・固定ログへ本文全文、version、guild／user ID、秘密、例外全文を複製しないことを確認した。`tests/test_post_commands.py::test_daily_detail_edit_v2_submit_reaches_service_with_all_field_semantics`、`tests/test_post_commands.py::test_detail_edit_channel_failures_use_destination_message_before_transaction`、`tests/test_post_commands.py::test_detail_edit_expected_version_conflict_still_refreshes_detail`、`tests/test_post_commands.py::test_detail_edit_modal_on_error_is_sanitized_and_preserves_parent`、既存の入力不正・全角終了日node群で、成功、no-op、入力・終了日・channel不正、認可、競合、DB／Presenter失敗、Modal on_errorを横断し、固定イベント、親View維持または最新View移譲、ephemeral、`AllowedMentions.none()`を確認した。`tests/test_post_commands.py::test_detail_edit_modal_has_type_specific_v2_labels_and_defaults`とPresenter上限node群でModal、Select、TextInput、EmbedのDiscord上限を確認した。

### 2026-08-30 管理者による他人予約削除理由隔離受入

- 実施者: Oto
- 証跡種別: Fake Interaction／専用PostgreSQLによる隔離受入

「管理者の他人削除理由」は`tests/test_schedule_deletion.py::test_required_delete_reason_rejects_missing_whitespace_and_too_long`、`tests/test_schedule_deletion.py::test_required_delete_reason_trims_edges_and_preserves_valid_content`、`tests/test_schedule_deletion.py::test_admin_other_required_reason_is_application_final_defense`で、空文字、半角・全角・制御空白、混合空白、501文字を型付きエラーで拒否し、1文字、trim対象、500文字、内部空白・改行、日本語を保存用理由として維持するDomain／Application境界を確認した。`tests/test_post_commands.py::test_delete_reason_modal_rejects_invalid_input_before_session_and_can_reopen`、`tests/test_post_commands.py::test_delete_reason_modal_passes_only_trimmed_valid_reason_to_delete_flow`、`tests/test_post_commands.py::test_closed_delete_reason_modal_can_be_reopened_without_retiring_detail`で、管理者の他作成者予約だけに有限timeoutの理由Modalを表示し、専用固定案内、Session開始前拒否、親Detail維持、再オープン、二重submit防止、fixed custom_id、ephemeral、`AllowedMentions.none()`を確認した。`tests/integration/test_schedule_deletion_integration.py::test_real_postgres_admin_other_invalid_reason_changes_nothing`、`tests/integration/test_schedule_deletion_integration.py::test_real_postgres_admin_deletes_other_owner_with_expected_kind`と既存の所有者・認可・guild・version・状態・rollback・同時削除node群で、拒否時のSchedule・run・OperationLog・NotificationLog非変更、有効なtrim済み理由を伴う1回の論理削除と1件のOperationLog、所有者本人の理由省略、操作時の最新認可・競合再検証を確認した。`tests/test_post_presenter.py::test_delete_preview_hides_audit_reason_and_shows_confirmation_without_mutation_claims`、`tests/test_post_presenter.py::test_deleted_embed_has_safe_fixed_result_without_thirty_day_promise`で、監査理由全文を利用者向け確認・成功Embedへ複製せず、Discordメッセージを削除しない固定案内を確認した。

### 2026-08-30 最終実Discord確認

- 実施者: Oto
- 証跡種別: 実Discord PC版／スマホ版

「応答時間」はPC版で、`/post show`の空入力、完全なchannel名検索、部分一致検索、`#`だけの安全な空候補、`/post resume`の候補表示をそれぞれ複数回確認した。各操作は概ね3秒以内に候補または空候補を返し、オプション読み込み失敗とアプリケーション応答失敗は再発しなかった。通常メッセージ送信とAutocomplete関連の異常ログはなく、一時停止した確認用予約は確認後に再開した。これにより本項目の全受入条件を満たすことを確認した。

「スマホ版 `/post delete`」は実スマホ版Discordで`public_id`候補の表示と選択を確認した。候補名から状態、種別、日時、投稿先、短縮IDを識別でき、100文字以内で、予約本文全文、内部DB ID、versionは表示されなかった。選択時は正しい予約IDが入力され、オプション読み込み失敗はなかった。候補表示・選択だけでは予約を削除せず、コマンドをcancelして実削除も行わなかった。他人の本文や予約情報は証跡へ残していない。これにより本項目の全受入条件を満たすことを確認した。

| 状態 | 確認項目 | 期待結果 |
|---|---|---|
| [x] | PC版 `/post show` | 閲覧可能な候補が3秒以内に表示され、deletedは削除済み表示になる |
| [x] | Web版 `/post edit` | 編集可能な候補だけが表示される |
| [x] | `/post edit` 変更指定なし | Autocomplete候補選択後を含め、予約IDだけでは専用案内をephemeral表示し、DB更新・OperationLog生成を行わない |
| [x] | スマホ版 `/post delete` | 100文字以内の候補名が識別でき、本文を含まない |
| [x] | PC／Web `/post pause` | activeのdaily／weeklyだけが表示される |
| [x] | PC／Web `/post resume` | 再開可能なpaused daily／weeklyだけが表示される |
| [x] | 最大件数 | 候補が最大25件で安定順に表示される |
| [x] | channel名完全一致 | `tester-a`で`#tester-a`への予約が表示される |
| [x] | channel名前方・部分一致 | `tester`で`#tester-a`、`#tester-b`、`お知らせ`で`#運営お知らせ`等が表示される |
| [x] | `#`・casefold・日本語 | `#一般`、英字の大文字入力、日本語channel名で同じ安全な検索結果になる |
| [x] | channel境界 | 閲覧不能、別guild、Thread、Category、Voice、DMの名前は候補検索に使われない |
| [x] | 入力拒否 | `#`だけ、制御文字、改行、ゼロ幅文字、100文字超は名前検索されず安全に空候補になる |
| [x] | 数値ID互換 | 数値channel ID完全一致でも従来どおり検索できる |
| [x] | 本人境界 | 許可ロール利用者には本人所有の予約だけが表示される |
| [x] | 管理者境界 | 管理者には設定guild内の他作成者の予約も表示される |
| [x] | 候補選択 | 選択後に完全なUUIDv7が入り、各コマンドを実行できる |
| [x] | 直接入力互換 | 候補を選ばず完全なcanonical UUIDv7を貼り付けて実行できる |
| [x] | 状態競合 | 候補表示後、実行前に状態・runを変えると既存の安全な拒否になる |
| [x] | 認可失敗 | DM、設定外guild、権限なしでは通常メッセージを送らず空候補になる |
| [x] | 情報境界 | 本文、秘密情報、内部ID、他guild情報が候補・利用者応答・通常ログに出ない |
| [x] | channel cache miss | 名前検索は該当なしとなり、REST取得せず、別条件の候補表示では短縮channel IDを使う |
| [x] | 応答時間 | 空入力・検索入力のどちらも3秒以内に候補または空候補が返る |
| [x] | 直接show詳細基盤 | `/post show`から共通`ScheduleDetailView`を表示する。長寿命Contextにはcanonical public_id、最新expected_version、actor ID、必要なlist originだけを保持し、guild IDや内部DB IDは保持・伝達しない。編集可能状態は✏️編集、activeのdaily／weeklyは⏸️一時停止、pausedのdaily／weeklyは▶️再開、削除可能状態は🗑️削除を表示し、直接show由来では「一覧へ戻る」を表示しない。操作不能なボタンは現在の仕様に従ってdisabled表示または非表示とし、version、内部DB ID、可否理由をEmbedへ表示しない。応答はephemeralかつ`AllowedMentions.none()`とする。各ボタン操作時にInteractionから現在guildを取得し、DMを拒否して、Interactionと最新Scheduleのguild境界、所有者／管理者認可、最新状態を再検証する。別guildや権限喪失時は安全な固定案内とし、fixed custom_idへguild ID、user ID、public_id、versionを含めない |
| [x] | 一覧詳細から戻る | 一覧で予約を選択後、同じephemeralメッセージの「一覧へ戻る」で元の状態・種類・ページ条件へ戻れる |
| [x] | 詳細中の一覧更新 | 詳細表示中に予約件数や状態が変わっても、戻ると最新一覧を取得し、消滅した末尾ページは最後の有効ページへ補正される |
| [x] | 一覧・詳細View長寿命化 | 15分経過後もList／DetailのButton・Selectが有効でcallbackへdispatchされ、最新認可・状態・version・run・attemptを再検証する。Bot再起動後の古い画面は復元せず、`/post list`／`/post show`再実行で復帰する |
| [x] | 詳細情報境界 | version、内部DB ID、操作可否の内部理由、秘密情報が詳細Embed・応答・通常ログに表示されない |
| [x] | 状態別詳細ボタン | 現在のSchedule状態、種別、run、attemptから操作可否をread-onlyで決定し、edit、pause、resume、deleteを状態マトリクスに従って表示する。processing、claimed、sending、unknown、不整合では安全側で操作不可とし、操作時に最新状態と認可を再検証する。fixed custom_idを使用し、予約IDやversion等を含めない |
| [x] | 詳細から一時停止 | `/post show`と一覧詳細の両方で一時停止でき、成功通知、paused詳細、注意事項へ更新される |
| [x] | 詳細から即時再開 | 保持投稿時刻前に再開でき、成功通知と最新active／draft詳細へ更新される |
| [x] | 詳細から再開4択 | 保持投稿時刻後に4択が表示され、各選択肢が既存再開規則どおり動作する |
| [x] | 再開cancel／timeout | 親`ScheduleDetailView`はBot稼働中操作可能とし、有限timeoutの`ResumeChoice`はcancelでDB更新せずpausedを維持して最新Detail Viewへ戻る。timeoutでもDB更新せずpausedを維持し、ResumeChoiceを操作不能にして親詳細を再取得する方法を案内する。待機中にSession、transaction、row lockを保持せず、Bot close時にViewとwait Taskを回収する |
| [x] | 詳細から作成者削除 | 作成者は理由入力なしで確認へ進み、成功後にdeleted詳細と固定通知が表示される |
| [x] | 管理者の他人削除理由 | 管理者が同じguildの他作成者予約を削除する場合だけ、前後空白除去後1～500文字の理由Modalを先に表示し、空文字、半角・全角・Unicode／制御空白だけ、501文字以上を固定理由へ変換せず拒否する。所有者本人は理由Modalなしの既存経路を維持する。submit時にguild、管理者権限、expected_version、状態、run／attemptを再検証し、失敗時はDB・ログを変更せず親Detailを維持する。有効理由だけをDeletion Serviceへ渡して同一transactionのOperationLogへ監査用に保存し、利用者向け応答・custom_id・通常ログへ理由全文や内部情報を複製しない |
| [x] | 削除cancel／timeout | 親`ScheduleDetailView`はBot稼働中操作可能とし、有限timeoutの`DeleteConfirm`はcancelでDB更新・削除・OperationLog生成を行わず最新Detail Viewへ戻る。timeoutでは削除せず、待機中にSession、transaction、row lockを保持しない。Bot close時にViewとwait Taskを回収する |
| [x] | 詳細操作競合 | 古い詳細からの操作が固定の競合案内で拒否され、最新詳細とボタンへ更新される |
| [x] | 詳細操作情報境界 | custom_id、Embed、応答、通常ログに予約本文、version、内部ID、理由、秘密情報、例外全文が出ない |
| [x] | 詳細編集ボタン | 編集可能時だけ有効で、操作不能時は先頭位置にdisabled表示される |
| [x] | 単発編集Modal | 投稿先、完全なJST投稿日時、本文の現在値が表示され、一度に編集できる |
| [x] | 毎日編集Modal | 投稿先、投稿時刻、終了日、本文の現在値が表示され、一度に編集できる |
| [x] | 毎週編集Modal | 投稿先、曜日、投稿時刻、終了日、本文の現在値が表示され、一度に編集できる |
| [x] | 詳細編集clear | 空欄で本文削除と終了日解除ができ、状態・run規則が維持される |
| [x] | 詳細編集no-op／競合 | no-opは更新せず固定案内、古いModalは競合案内と最新詳細へ戻る |
| [x] | 詳細編集認可・channel境界 | 本人・管理者境界と同guild TextChannel・Bot権限をsubmit時に再確認する |
| [x] | 詳細編集Modal lifecycle | 二重submitを防止し、close／15分timeout後も親詳細から再度編集できる |
| [x] | 詳細編集後の一覧復帰 | 最新詳細の操作と一覧由来Contextを維持し、戻ると最新一覧へclampされる |
| [x] | 詳細編集表示・情報境界 | 本文全文や内部情報を通知・custom_id・通常ログへ出さず安全に表示される |
