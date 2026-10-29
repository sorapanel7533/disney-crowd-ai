# 運用・復元手順

## 保存

原本 `disneyland.db` / `disneysea.db` は変更しない。実行時は `.runtime/` にコピーして列追加で移行する。`DISNEY_DB_DIR` で保存先を指定できる。観測の一意キーは提供元・パークID・施設ID・提供元UTC観測時刻。閉園/運休/0分/欠測を別々に保存する。

GitHub Actionsが `crowd-data` ブランチの2個のSQLiteと `last-run.json` を更新する。コードのmainブランチとは別。UI起動/閲覧は不要。標準公開リポジトリランナーを使い、有料サービス契約は追加しない。08:07〜21:52 JSTの15分間隔。GitHubのスケジュールは遅延・欠落があり厳密な15分保証ではない。concurrencyで同じジョブを並行実行しない。

UIは15分ごとに外部スナップショットを読み込み、既存ローカル行を消さずにマージ。公式の定期保存予測は21時台の最初のジョブが作った翌日予測。ジョブ時刻そのものを記録し、21:00ちょうどに作ったとは表示しない。ユーザーの画面で新たに作成した一時予測・管理者手入力はローカル保存のため、必要なら管理画面のDBバックアップを取得する。永続保存済みと区別する。

Streamlitのローカルファイル永続性は保証されない。外部同期の失敗時はローカル/原本の履歴を残し、同期状態に失敗を表示する。外部保存先のDBが大きくなったら月別アーカイブへ分割する。100MiBを超える同期は停止する。Git履歴を勝手に圧縮・削除しない。

## 起動・テスト

Python 3.12で `pip install -r requirements.txt`、`streamlit run app.py`。Linuxは `packages.txt` の日本語フォントも必要。

回帰テストは `pip install pytest` の後、`DISNEY_DISABLE_REMOTE_SYNC=1 python -m pytest -q`。実データ収集は `python collector.py --data-dir .runtime --force --context`。`--force` は営業時間外の単発診断用。通常の定期ジョブは08〜22時JST以外にAPIアクセスしない。手動実行および収集コードをmainへ反映した直後は、時間外でも1回だけ接続と保存を診断する。

## 管理者

Streamlit Secretsの `ADMIN_PASSWORD_SHA256` または環境変数 `DISNEY_ADMIN_PASSWORD_SHA256` に、十分長い管理者パスワードのSHA-256を設定する。値をGitへ保存しない。未設定では変更操作は無効。全削除ボタンは常に無効。DPA訂正前は `dpa_revisions` に保全。

管理者の手入力はローカルDBに保存される。再起動前には整合性のあるDBバックアップをダウンロードする。バックアップを収集用DBにマージしてGitHub保存先へ反映すれば以降の再起動でも復元できる。管理者パスワード自体をこのリポジトリやデータブランチへ置かない。

## バックアップと復元

1. 管理画面のDBバックアップ、または `persistence.snapshot(conn, path)` でSQLite backup APIを使う。稼働中DBを単にコピーしない。
2. 復元前に現行DBもバックアップする。復元先の別ディレクトリで `PRAGMA integrity_check` が `ok` か確認する。
3. `connect_db` による追加型移行後、`persistence.merge_snapshot(conn, backup_path)` で既存行を保持してマージする。
4. 件数、日数、対象施設、重複キー、評価済み予測を確認して保存先を切り替える。元のバックアップは消さない。
5. GitHubデータブランチの過去コミットも復元用スナップショット。以前のDBを読み取り、同じマージ手順で復元する。

## 障害

API障害は `collection_runs` にstatus/message/時刻を記録。HTTP再試行は上限1回、次の定期ジョブでも再試行。最後の正常データは現在値として使わず、時刻を付けて別表示。GitHub Actionsが失敗した場合はActionsログと `last-run.json` を確認する。深夜の単発検証以外に閉園中の無駄な常時アクセスはしない。

DisneyRealの画像ページはOCRの依存環境・施設対応・誤読率が検証されるまで保存しない。30/90/180/365日の選択は維持し、最大7日を選択、構造失敗で中断する。取得成功済みの日は再取り込みしない。架空の実績で穴埋めしない。

## 根拠

- Queue-Times: https://queue-times.com/pages/api （5分更新・UTC・帰属表示）
- Streamlit: https://docs.streamlit.io/develop/concepts/connections/connecting-to-data （ローカル永続性）
- GitHub: https://docs.github.com/en/actions/how-tos/troubleshoot-workflows （スケジュール遅延・停止）
- GitHub: https://docs.github.com/en/billing/concepts/product-billing/github-actions （公開標準ランナー無料）
- DPA対象: https://www.tokyodisneyresort.jp/tdr/guide/app_service/disneypremieraccess.html
- PP終了: https://faq.tokyodisneyresort.jp/answer/67eaa65938a1ebab2162dc9e/
