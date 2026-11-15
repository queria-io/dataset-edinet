# dataset-edinet

Queria のデータセット `edinet`（EDINET 開示データ）のリポジトリです。データセットの説明は [docs/index.md](docs/index.md) にあります。

## 更新

毎日 EDINET の書類一覧 API を日付単位で取得します。未取得の日だけを取得し（進捗は `_source.fetch_progress` に記録）、書類情報の修正・取下げを取り込むため直近 7 日分は毎回取得し直します。

1 回の実行で取得する日数には上限（`EDINET_MAX_DATES_PER_RUN`、既定 1000）があります。全期間を 1 回で取得すると CI のジョブの時間制限を超えるため、毎回ビルドと公開まで完了させ、進捗を `fetch_progress` に残します。次回の実行がその進捗を引き継ぐので、日次の実行で数日かけて履歴（新しい日付から順）が埋まります。遡って取得する開始日は環境変数 `EDINET_BACKFILL_START`（既定 `2016-01-01`）で変えられます。

財務データは、書類一覧で xbrlFlag=1 の有価証券報告書・半期報告書を、書類取得 API（type=1 の XBRL）から書類ごとに取得します。インスタンスの値・context と、会社独自の項目の名前（ZIP 内の `_lab.xml`）を書かれたまま入れ、相対年度・連結/個別・項目名は dbt で付けます。取得済みの書類は `_source.xbrl_fetch_progress` に記録します。1 回の実行の書類数の上限は `EDINET_MAX_XBRL_DOCS_PER_RUN`（既定 1000）、取得をやめる時刻は `EDINET_FETCH_UNTIL`（`05:00` の形。夜間だけ取得するとき）、対象の書類の種類は `EDINET_XBRL_DOC_TYPES`（既定 `120,160`）で変えられます。過去の書類は新しい提出日から順に取り直している途中で、まだのものは以前に取った type=5 の CSV の行を使います（`stg_financial_facts.source` が `csv`）。

`EDINET_XBRL_DIR` にフォルダを渡すと、取得した type=1 の ZIP をそのフォルダに `<doc_id>.zip` で置き、SHA-256 とバイト数を `_source.xbrl_files`（`main.raw_xbrl_files`）に記録します。`raw_xbrl_files.sha256` はアセットの列なので、ビルドの `fdl commit --assets` に同じフォルダを渡すと、リポジトリに無い ZIP だけが `assets/<sha256>` に上がります。fdl でのビルドでは、公開中のバージョンが既に持つ ZIP を取り込みの最初に手元から消します。ZIP を残す前に取得した書類は、未取得の書類を取り終えたあと、残りの上限の範囲で新しい提出日から順に取り直します。

標準の要素の名前は、金融庁が年 1 回公表する EDINET タクソノミから取ります。公表ページ（https://www.fsa.go.jp/search/）からまだ入れていない年版を見つけて `_source.taxonomy_labels` に入れるので、新しい年版の追加に手作業は要りません。直近 1 年の書類に名前の付かない要素があると、dbt のテスト（`stg_financial_facts_labels_present`）が失敗します。

```bash
git submodule update --init
uv sync
FDL_TOKEN=... bash scripts/build.sh
```

`scripts/build.sh` は公開中のバージョンを `fdl checkout` してから取り込み・ビルドし、`fdl commit` と `fdl push` で公開します。手元で実行するときは、queria.io で発行したトークンを `FDL_TOKEN` に渡します。GitHub Actions では OIDC で取得します。

取り込みと dbt のビルドは、別々に実行して公開できます。ビルドやテストが失敗しても、先に公開した取り込みの分は残ります。取り込みだけのときも、公開する記述にモデルの SQL を載せるために `dbt compile` は実行します。

```bash
bash scripts/build.sh python main.py ingest   # 取り込みだけ（モデルは作り直さない）
bash scripts/build.sh python main.py build    # 新しいタクソノミの取り込みと、dbt のビルドとテスト
```
