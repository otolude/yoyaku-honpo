# 無課金リリース準備 / Lightsail運用Runbook

この文書はAWSリソースを作成しない。対象は東京リージョンのUbuntu、1 GB RAM / 2 vCPU / 40 GBを想定する。AI機能は無効のままとし、有料APIを呼ばない。

## 課金開始前に完了すること

1. production Compose、systemd unit、backup scriptをrelease commitで固定する。
2. app imageとPostgreSQL imageをbuild/scanし、registry上のdigestを記録する。productionではtagでなくdigestをcompose.envへ設定する。
3. production.env.exampleをprivateなproduction.envへコピーし、IDだけを設定する。command syncと全AI flagはdisabledを維持する。
4. deployment/production/secretsを0700、各secret fileを0600、root所有で作る。値をshell引数、画面、log、Gitへ出さない。
5. Discord tokenとDB passwordは新規発行する。ローテーションは新credential配置、接続確認、旧credential失効の順で行う。

## インスタンス作成後の最短手順

1. OS更新、Docker EngineとCompose pluginを公式手順で導入し、bootstrap-ubuntu.sh --checkで前提を確認する。
2. SSH鍵のみを許可しpassword/root loginを無効化する。Lightsail firewallとUFWはSSHを管理元IPだけに限定する。アプリ用inbound portを開けず、PostgreSQL 5432をhostへpublishしない。
3. 2 GiB swapfileをroot:root 0600で作りvm.swappiness=10とする。既存swapがあれば重複作成しない。
4. releaseを/opt/discord-ai-reminder-bot/releases/<commit>へ配置しroot所有・非書込にしてcurrent symlinkを切り替える。
5. private設定とsecretを配置しdocker compose -f compose.production.yaml config --quietで静的検証する。
6. imageをdigest指定でpullする。postgresだけ起動してhealthyを確認し、手動承認した一回限りのmigrate profileを実行する。downgradeは実行しない。
7. appを起動しcurrent boot/current invocationのjournalでdatabase_schema_verifiedの後にstartup_recovery_completeが1回出ることを確認する。Discord投稿やcommand syncをhealthcheckに使わない。
8. stack serviceとbackup timerをinstall/enableする。初回backupを別の一時PostgreSQLへrestoreし、Alembic current/checkとtable countを確認する。本番DBへrestoreしない。

## deploy / rollback

deployは新release directoryと新app digestを用意し、DB backup、migration、app切替の順に行う。migration前にbackupとchecksumを確認する。health markerが成立しなければappを停止し、currentとapp digestを直前releaseへ戻す。DB downgradeは自動実行しない。schema非互換なら停止状態を維持し、復元は別の明示判断とする。

## 障害復旧と日次運用

- 毎日: stack稼働、restart count、disk、memory/swap、直近backupとchecksumを確認する。
- 毎週: OS/imageのsecurity update候補、log量、残容量を確認する。
- 毎月: isolatedな一時DBで最新backupの復元試験を行い、成功日時とrelease commitだけ記録する。
- DB障害: appを停止しvolumeを上書きせず、新volumeへ検証済みdumpをrestoreして切り替える。
- token漏えい疑い: app停止、新token発行、secret file atomic置換、起動確認、旧token失効。値は記録しない。

## 最終チェックリスト

- [ ] Git clean、local/upstream一致、CI成功
- [ ] app/PostgreSQL imageがdigest固定、脆弱性レビュー済み
- [ ] production secretがGit外、root所有0600、token新規発行済み
- [ ] AI/command sync無効、有料API credentialなし
- [ ] 5432非公開、SSHは鍵+管理元IP限定、UFW有効
- [ ] 2 GiB swap、resource limit、log rotation設定済み
- [ ] migration前backup、restore rehearsal成功
- [ ] readiness marker順序、restart、reboot、graceful shutdown確認済み
- [ ] rollback先commit/digest記録済み
- [ ] 月額$7 Lightsail作成についてユーザーが最終承認済み
