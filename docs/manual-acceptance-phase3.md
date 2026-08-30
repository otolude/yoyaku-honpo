# Phase 3 受入

Phase 3の受入をPhase 1・Phase 2から分離して記録する。現時点では第1段階の`/post show` Autocomplete改善だけを対象とし、AI予約名など後続段階は収録しない。自動テストで確認できる項目と実Discord確認が必要な項目を分け、実Discordの証跡がない項目は確認済みにしない。

- 実施日: 2026-08-30
- 実施者: Codex（自動テスト）
- 証跡: 重点テスト322件、通常pytest 815件成功／297件skip、専用PostgreSQL込み全pytest 1112件成功
- 集計: 確認済み 16件／未確認 0件（合計16件）
- Phase 3第1段階受入判定: 完了

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
