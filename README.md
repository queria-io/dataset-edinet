## データ出典

[金融庁 EDINET](https://disclosure2.edinet-fsa.go.jp/) から取得した、開示書類のメタデータ（書類一覧）と各種マスタです。

- 書類一覧（`mart_documents`）: 書類一覧 API（documents.json, Version 2）。有価証券報告書・四半期報告書・半期報告書・臨時報告書・大量保有報告書・公開買付関連書類などの提出書類を、1行1書類で収録。
- 企業マスタ（`mart_companies`）: EDINETコードリスト（EdinetcodeDlInfo.csv）。EDINET に提出者として登録された企業・組合・外国法人等を、1行1提出者で収録。
- ファンドマスタ（`mart_funds`）: ファンドコードリスト（FundcodeDlInfo.csv）。投資信託・ETF 等の特定有価証券のファンドを、1行1ファンドで収録。
- 財務ファクト（`stg_financial_facts`）: 書類取得 API（type=5 CSV）。有価証券報告書の財務データ本体を、要素ID×コンテキストの縦持ちで生に忠実に収録。
- 主要経営指標（`mart_business_results`）: 上記を「企業×決算期×標準指標」に正規化したワイドテーブル。
- 従業員・女性活躍の指標（`mart_human_capital`）: 上記から有報の従業員の状況・女性活躍と育児休業の指標・女性役員を1書類1行にまとめた表。

財務数値は有価証券報告書の XBRL を構造化した CSV（type=5）から取り込みます。書類の本文テキスト（PDF）や添付書類は含みません。財務データは現在、有価証券報告書のみ対象で、四半期・半期報告書は今後対応予定です。提出時点の本体のみで、訂正報告書による訂正は未反映です。全期間は段階的に充填中です。

閲覧可能期間は書類種別ごとに最長10年（縦覧期間＋延長期間）で、それを過ぎた書類は API から取得できなくなります。

## テーブル: mart_documents

提出書類のメタデータ。提出者法人番号（corporate_number）を持つため、houjin_bangou / gbizinfo と法人番号で結合できます。

主なカラム:

- doc_id: 書類管理番号（EDINET が書類ごとに採番する一意の番号）
- seq_number: ファイル日付ごとの連番
- edinet_code: 提出者 EDINET コード
- sec_code: 提出者証券コード（上場会社のみ）
- corporate_number: 提出者法人番号（13桁。houjin_bangou / gbizinfo と結合可能）
- filer_name: 提出者名
- fund_code: ファンドコード
- ordinance_code / form_code: 府令コード / 様式コード
- doc_type_code / doc_type_name: 書類種別コード / 書類種別名（有価証券報告書、四半期報告書、大量保有報告書 等）
- period_start / period_end: 対象期間（自 / 至）
- submit_datetime / submit_date: 提出日時 / 提出日
- doc_description: 提出書類概要
- issuer_edinet_code / subject_edinet_code / subsidiary_edinet_code: 発行会社 / 対象 / 子会社 EDINET コード
- current_report_reason: 臨時報告書の提出事由
- parent_doc_id: 親書類管理番号（訂正・変更報告書等の基となる書類）
- ope_datetime: 書類情報修正の操作日時
- withdrawal_status / is_withdrawn: 取下区分 / 取下げ書類か
- doc_info_edit_status: 書類情報修正区分
- disclosure_status: 開示不開示区分
- has_xbrl / has_pdf / has_attach_doc / has_english_doc / has_csv: 各ファイルの有無
- legal_status: 縦覧区分（1=縦覧中, 2=延長期間中, 0=閲覧期間満了）

## テーブル: mart_companies

EDINET 提出者の企業マスタ。EDINETコード（edinet_code）で `mart_documents` と、提出者法人番号（corporate_number）で houjin_bangou / gbizinfo と結合できます。

主なカラム:

- edinet_code: EDINETコード（E+5桁。`mart_documents` と結合可能）
- submitter_type: 提出者種別（内国法人・組合、外国法人 等）
- listing_status: 上場区分（上場 / 非上場）
- is_consolidated: 連結の有無
- capital: 資本金（出典CSVの単位のまま）
- fiscal_year_end: 決算日（"5月31日" 等の月日テキスト）
- filer_name / filer_name_en / filer_name_kana: 提出者名 / 英字名 / ヨミ
- address: 所在地（市区町村名から始まり、都道府県名は付かない）
- industry: 提出者業種
- sec_code: 証券コード（末尾0付き5桁。非上場は空）
- corporate_number: 提出者法人番号（13桁。houjin_bangou / gbizinfo と結合可能）

書類一覧との結合例:

```sql
select d.doc_id, d.filer_name, c.industry, c.listing_status, c.capital
from mart_documents d
join mart_companies c using (edinet_code)
limit 20
```

本店の市区町村コードは、法人番号で houjin_bangou（国税庁 法人番号公表サイト）につなぐと取れます。上場会社はほぼすべて（2026年10月時点で 3,808 社中 3,805 社）が見つかります。houjin_bangou は登記上の本店の最新の所在地なので、EDINET の `address` と市区町村が食い違う会社が数十社あります（移転や、実際の本社と登記上の本店が違う場合）。`lg_code` で e-Stat などの市区町村別の統計と重ねられます。

市区町村ごとに本店のある上場会社を数える例:

```sql
select h.lg_code, h.prefecture_name, h.city_name, count(*) as companies
from edinet.main.mart_companies c
join houjin_bangou.main.mart_houjin_bangou h using (corporate_number)
where c.listing_status = '上場'
group by all
order by companies desc
```

## テーブル: mart_funds

EDINET のファンドマスタ。投資信託・ETF 等の特定有価証券のファンドを収録します。書類一覧（`mart_documents`）はファンド書類のとき `fund_code` を持つため、ファンド名や区分を解決できます。発行者（運用会社）の `edinet_code` で `mart_companies` とも結合できます。

主なカラム:

- fund_code: ファンドコード（G+5桁。`mart_documents` の fund_code と結合可能）
- sec_code: 証券コード（上場ファンド等のみ。多くは空）
- fund_name / fund_name_kana: ファンド名 / ヨミ
- security_type: 特定有価証券区分名（内国投資信託受益証券、外国投資信託受益証券 等）
- specified_period_1 / specified_period_2: 特定期（決算期の月日テキスト）
- edinet_code: 発行者（運用会社）の EDINETコード（`mart_companies` と結合可能）
- issuer_name: 発行者名

ファンド書類との結合例:

```sql
select d.doc_id, f.fund_name, f.security_type, f.issuer_name
from mart_documents d
join mart_funds f using (fund_code)
limit 20
```

## テーブル: stg_financial_facts

有価証券報告書の財務データ本体を、生に忠実な縦持ちファクトで収録します。1行 = 1要素 × コンテキスト。要素ID（`element_id`、XBRL 標準タクソノミ。例 `jppfs_cor:NetSales`）が企業横断比較の鍵です。`doc_id` で `mart_documents` と、`corporate_number` で houjin_bangou / gbizinfo と結合できます。財務本表（jppfs）も企業情報（jpcrp）も主要経営指標（jpcrp の Summary 系）も、絞り込まずすべて収録しています。

主なカラム:

- doc_id: 書類管理番号（`mart_documents` と結合可能）
- edinet_code / sec_code / corporate_number / filer_name: 提出者情報（documents から付与）
- period_end / submit_date: 会計期間（至）/ 提出日
- element_id: 要素ID（XBRL 標準タクソノミ。横断比較の鍵）
- item_name: 項目名（日本語ラベル）
- context_id: コンテキストID（期間・連結区分・セグメント次元の識別子。連結/個別は _NonConsolidatedMember、年度は CurrentYear / PriorNYear、セグメントは ..._...Member で判別）
- relative_year: 相対年度（フロー項目は 当期 / 前期 …、ストック項目は 当期末 / 前期末 …）
- consolidated_individual: 連結・個別（主要経営指標の行は その他 固定。連結区分は context_id で判別）
- period_instant: 期間・時点
- unit / unit_id: 単位（円 / 株 / ％ 等）
- value: 生の値（数値・テキストブロックを含む）
- numeric_value: value を数値化したもの（数値でなければ NULL）
- csv_type: CSV 様式コード（jpcrp030000-asr=有報本体 / jpaud=監査報告書 / jpsps=投信 等）
- row_seq: CSV 内の行番号

全社の特定科目を横断抽出する例（連結・当期の総額。セグメント内訳を除くため context_id で絞る）:

```sql
select edinet_code, filer_name, period_end, numeric_value as net_sales
from stg_financial_facts
where element_id = 'jppfs_cor:NetSales'
  and context_id = 'CurrentYearDuration'
order by numeric_value desc
limit 20
```

## テーブル: mart_business_results

有報「主要な経営指標等の推移」を「企業×会計年度×標準指標」に正規化したワイドテーブル。連結優先（連結があれば連結、無ければ個別）で1書類×会計年度=1行。会計基準（JP GAAP / IFRS）を問わず同じ列に揃え（IFRS 採用企業は RevenueIFRS 等の要素を JP GAAP 名と統合）、複数社・複数年の指標比較に使えます。1書類あたり最大5会計年度分（当期〜四期前）の行を持ちます。

主なカラム:

- doc_id / edinet_code / sec_code / corporate_number / filer_name: 識別情報
- period_end / year_offset / fiscal_year: 当期会計期間（至）/ 年度オフセット（当期=0 … 四期前=-4）/ 実年度
- net_sales / ordinary_income / profit_before_tax / net_income / profit_attributable_to_owners / comprehensive_income: 売上高 / 経常利益（JP GAAP のみ）/ 税引前利益（IFRS）/ 当期純利益 / 親会社株主帰属当期純利益 / 包括利益
- net_assets / total_assets / capital_stock / total_issued_shares: 純資産額 / 総資産額 / 資本金 / 発行済株式総数
- net_assets_per_share / basic_eps / diluted_eps: 1株当たり純資産（BPS）/ 1株当たり当期純利益（EPS）/ 潜在株式調整後EPS
- equity_to_asset_ratio / roe / per / payout_ratio: 自己資本比率 / ROE / PER / 配当性向
- dividend_per_share / interim_dividend_per_share: 1株当たり配当額 / 中間配当額
- cash_and_equivalents / operating_cash_flow / investing_cash_flow / financing_cash_flow: 現金及び現金同等物残高 / 営業・投資・財務キャッシュ・フロー

複数社の売上高推移を比べる例:

```sql
select filer_name, fiscal_year, net_sales
from mart_business_results
where sec_code in ('27530', '79740')
order by filer_name, fiscal_year
```

## テーブル: mart_human_capital

有価証券報告書の「従業員の状況」「女性活躍・育児休業の指標」「役員の状況」を、1書類=1行にまとめた表です。値はその有報の当期分だけで、年をまたぐ推移は各年の有報の行を並べて追います。女性活躍・育児休業の指標は、2024年以降に提出された有報から数値で取れます（2023年提出分は本文の文章にしか無く、この表では空です）。平均年齢・平均勤続年数・平均年間給与と女性役員は 2019年提出分から入っています。

主なカラム:

- doc_id / edinet_code / sec_code / corporate_number / filer_name: 識別情報
- period_end / fiscal_year / submit_date: 当期会計期間（至）/ 会計年度（period_end の年）/ 提出日
- number_of_employees_consolidated / number_of_employees: 従業員数（連結 / 提出会社）
- temporary_workers_consolidated / temporary_workers: 平均臨時雇用人員（連結 / 提出会社）
- average_age / average_length_of_service / average_annual_salary: 平均年齢・平均勤続年数（年）・平均年間給与（円）。提出会社の値
- female_manager_ratio: 管理職に占める女性労働者の割合（提出会社、0〜1）
- female_to_male_wage_ratio / _regular / _non_regular: 男女の賃金の差異（男性の賃金に対する女性の賃金の割合。全労働者 / 正規 / 非正規）
- male_childcare_leave_rate / male_childcare_leave_rate_basis: 男性労働者の育児休業取得率と、その算出根拠（育児・介護休業法施行規則71条の4第1号・第2号、女性活躍推進法のどれか）
- female_officers / female_officer_ratio: 女性役員の人数と比率（提出日時点。提出日時点の値が無い書類は当期末時点）

有報の開示どおりに取り込んだうえで、次のように整えています。

- 男女の賃金の差異に % のまま入力された値（71.4 など）は 100 で割って比率に直す（2 を超える値が対象）
- 女性管理職比率と女性役員の比率は 1 を超える値を % とみなして 100 で割る
- 平均年間給与が 10 万円未満か 5,000 万円を超える値は桁の入力誤りとみなして NULL にする。平均臨時雇用人員の負の値も NULL にする
- 平均年齢・平均勤続年数を年と月に分けて開示している会社は、年に換算して足す
- 連結子会社ごとの指標は、数値に子会社名が付いていないので含めない

業種ごとに平均年間給与と男女の賃金の差異を比べる例:

```sql
with latest as (
    select *
    from mart_human_capital
    qualify row_number() over (partition by edinet_code order by period_end desc, doc_id desc) = 1
)
select
    c.industry,
    count(*) as companies,
    median(l.average_annual_salary) as median_salary,
    median(l.female_to_male_wage_ratio) as median_wage_ratio
from latest l
join mart_companies c using (edinet_code)
group by c.industry
order by median_salary desc
```

## 更新

毎日 EDINET の書類一覧 API を日付単位で取得します。未取得日のみ取得し（進捗は `_source.fetch_progress` に永続化）、直近7日分は書類情報修正・取下げを取り込むため毎回再取得します。

1回の実行で取得する日数には上限（`EDINET_MAX_DATES_PER_RUN`、既定 1000）を設けています。全期間バックフィルを1回で走らせると CI のジョブ上限を超えるため、毎回 build + push まで完走させ、進捗を `fetch_progress` に残します。`queria pull` が次回その進捗を取り込むので、日次実行で数日かけて履歴（新しい日付から順）が埋まります。

遡及取得の開始日は環境変数 `EDINET_BACKFILL_START`（既定 `2016-01-01`）で調整できます。ローカル検証では直近に絞ると初回ビルドが軽くなります。

企業マスタ（`mart_companies`）・ファンドマスタ（`mart_funds`）は、それぞれ EDINETコードリスト・ファンドコードリストを毎回全件ダウンロードし、全件入れ替えで最新スナップショットに更新します（APIキー不要）。

財務データ本体（`stg_financial_facts` / `mart_business_results` / `mart_human_capital`）は、書類一覧で csvFlag=1 の有価証券報告書を書類取得 API（type=5 CSV）から書類単位で取得します。取得済み書類は `_source.financial_fetch_progress` に記録し、CSV が無い書類は status='empty' として再取得しません。新しい提出日から順に充填します。1回の実行の書類数上限は `EDINET_MAX_DOCS_PER_RUN`（既定 1000）、対象書類種別は `EDINET_FINANCIAL_DOC_TYPES`（既定 `120`=有価証券報告書。四半期 140・半期 160 等へカンマ区切りで拡張可能）で制御します。

## クレジット

このサービスは、金融庁 EDINET の書類一覧 API を使用していますが、提供情報の最新性、正確性、完全性等が保証されたものではありません。

## ライセンス

[金融庁 EDINET 利用規約](https://disclosure2.edinet-fsa.go.jp/)
