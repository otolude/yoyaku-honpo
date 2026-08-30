# 機能フロー

## 1. 予約の作成と通常投稿

```mermaid
sequenceDiagram
    actor User as Discord user
    participant Bot as Bot presentation
    participant App as Application service
    participant DB as PostgreSQL
    participant Worker as Schedule worker
    participant Gateway as Discord gateway

    User->>Bot: create once, daily, or weekly
    Bot->>App: validated input and current actor context
    App->>DB: transaction creates Schedule and OperationLog
    DB-->>Bot: commit result DTO
    Bot-->>User: ephemeral success with display name or JST fallback
    Worker->>DB: claim due run with row lock
    Worker->>Gateway: send message outside claim transaction
    Gateway-->>Worker: sent, retryable, or unknown result
    Worker->>DB: finalize run and delivery attempt
```

JSTフォールバック名は表示時の純粋関数で、DBへ保存せず本文を一覧・Select・Autocompleteへ出さない。投稿の一時エラーには上限付きretryがある一方、結果不明では二重投稿防止を優先する。

## 2. 詳細操作、競合、削除

| 操作 | transaction前 | transaction内 | 結果 |
| --- | --- | --- | --- |
| 一時停止・再開 | guild、owner／administrator、状態を確認 | Scheduleをlockし、expected versionと現在状態を再検証 | 実変更だけversionとOperationLogを更新 |
| 本文・日時編集 | Modalまたはcommand入力を検証 | 同じSchedule行で再認可、再検証、原子的更新 | staleなら固定競合案内と安全に取得できる最新詳細 |
| 手動予約名 | 現在名をModal初期値に使用 | source、状態、expected versionを再検証 | 設定・変更はmanual、空欄はunset、同値はno-op |
| 論理削除 | 対象と操作権限を確認 | lock後にversion・状態・管理者理由を再検証 | deletedとして保持し、30日cleanup対象へ移す |

詳細Viewを開いた後に権限を失った場合は古いEmbedとViewを解除し、他人の本文、予約名、投稿先、UUIDを再表示せず固定案内だけを返す。外側Modalのcustom IDには非識別nonceを付け、同種Modalを複数保持しても別instanceを停止しない。

## 3. AI予約名のJob登録とCAS保存

```mermaid
sequenceDiagram
    participant App as Schedule application service
    participant DB as PostgreSQL
    participant Worker as Name generation worker
    participant Port as NameGenerator Port

    App->>DB: create or content edit transaction
    App->>DB: insert pending Job for expected Schedule version
    Note over App,DB: disabled, manual, clear content, or no-op creates no Job
    Worker->>DB: lock Schedule then Job
    Worker->>DB: lock daily then monthly Budget and reserve pessimistic cost
    Worker->>DB: mark processing and commit
    Note over Worker,Port: Session, transaction, row lock, and ORM are closed
    Worker->>Port: generate once with immutable request
    Port-->>Worker: validated name or fixed error class
    Worker->>DB: short finalize transaction with conservative CAS
    Note over Worker,DB: manual, stale, no content, or terminal discards result
```

成功時だけ`display_name`と`source=ai`を保存し、利用者操作用Schedule versionと`updated_at`は増やさない。system OperationLogへ生成名全文を保存しない。Provider timeout、cancel、usage不明でも予約済みBudgetを返却せず、自動retry、model fallback、基本予約の失敗を行わない。OpenAI Adapterは初期disabledで、実Provider受入は未確認である。

## 4. startup recovery、poll、shutdown、cleanup

```mermaid
flowchart TD
    S[Bot startup] --> V[Verify schema revision]
    V --> R[Run schedule, notification, and name recovery]
    R --> P[Start eligible poll loops]
    P --> C{Shutdown requested}
    C -->|no| P
    C -->|yes| X[Stop new polls]
    X --> A[Cancel and await active tasks]
    A --> M[Stop and await views and modals]
    M --> D[Dispose database engine]

    K[Cleanup cycle] --> T[Delete eligible terminal records]
    T --> J[Delete terminal name Jobs after retention]
    J --> B[Delete expired Budget buckets]
```

起動時Recoveryはpoll開始前に行う。予約名生成のlease切れ`processing`はProviderを再呼出しせず`startup_abandoned`へ移し、`pending`は維持する。cleanupはpending／processing Jobを削除せず、ScheduleのFK blockerと保持順序を尊重する。

## 5. Migration安全ラッパー

```mermaid
flowchart TD
    C[Explicit wrapper command] --> P[Classify operation]
    P --> E[Validate target and expected database]
    E --> W{Write or autogenerate}
    W -->|yes| F[Validate exact operation confirmation]
    W -->|no| U[Select allowed process URL source]
    F --> U
    U --> N[Check database name encoded in URL]
    N --> Q[Connect and query current_database]
    Q --> M{Exact match}
    M -->|no| R[Reject before migration]
    M -->|yes| Z[End verification transaction]
    Z --> G[Start Alembic migration context]
```

正式経路はPythonラッパーだけで、Alembic CLI直接実行、offline mode、未知commandを`alembic/env.py`の最終ガードでも拒否する。test、development、productionでURL選択を分離し、URLや資格情報をログへ出さない。

## 6. 情報境界

Interaction応答はephemeralと`AllowedMentions.none()`を必要箇所で使用し、拒否時に内部例外や他人予約を露出しない。AI Job／Budgetには本文、prompt、応答、生成名、Discord ID、UUID、契約情報を保存しない。詳細な境界は[安全性とプライバシー](security-and-privacy.md)を参照する。
