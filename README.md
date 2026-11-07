# EDINET 開示書類・財務データ

金融庁 [EDINET](https://disclosure2.edinet-fsa.go.jp/) の開示書類の一覧、提出者とファンドのマスタ、有価証券報告書・半期報告書の財務データを、SQL で検索・集計できるテーブルにしたデータセットです。主要経営指標は会計基準をまたいで同じ列に統一しているので、複数の会社・複数の年の比較に使えます。提出者の法人番号で houjin_bangou・gbizinfo と結合できます。

## 出典と利用条件

| 原典 | テーブル |
| --- | --- |
| 書類一覧 API（documents.json、Version 2） | `main.mart_documents` |
| EDINETコードリスト（EdinetcodeDlInfo.csv） | `main.mart_companies` |
| ファンドコードリスト（FundcodeDlInfo.csv） | `main.mart_funds` |
| 書類取得 API（type=1 の XBRL） | `main.mart_affiliated_entities` |
| 書類取得 API（type=5 の CSV。XBRL を構造化したもの） | `main.stg_financial_facts`・`main.mart_business_results`・`main.mart_human_capital` |

- [EDINET の利用規約](https://disclosure2.edinet-fsa.go.jp/)に従います
- このサービスは、金融庁 EDINET の書類一覧 API・書類取得 API・EDINETコードリスト・ファンドコードリストを使用していますが、提供情報の最新性、正確性、完全性等が保証されたものではありません

## テーブル

| テーブル | 内容 |
| --- | --- |
| `main.mart_documents` | 1 行が 1 書類。有価証券報告書・四半期報告書・半期報告書・臨時報告書・大量保有報告書・公開買付関連書類などのメタデータ |
| `main.mart_companies` | 1 行が 1 提出者。企業・組合・外国法人などの業種・市場区分・資本金・決算日・法人番号 |
| `main.mart_funds` | 1 行が 1 ファンド。投資信託・ETF などの特定有価証券のファンド名・区分・発行者 |
| `main.stg_financial_facts` | 1 行が 1 要素・1 コンテキスト。有価証券報告書・半期報告書の財務データの本体 |
| `main.mart_business_results` | 1 行が 1 書類・1 会計年度。有報の主要経営指標を会計基準をまたいで統一したもの |
| `main.mart_human_capital` | 1 行が 1 書類。有報の従業員の状況・女性活躍と育児休業の指標・女性役員 |
| `main.mart_affiliated_entities` | 1 行が 1 書類・1 関係会社。有報の関係会社の状況（親会社・連結子会社・持分法適用関連会社などの名称・住所・資本金・事業・議決権の所有割合・関係内容） |

テーブルの結合:

- `mart_documents` と `mart_companies` は `edinet_code`、ファンドの書類と `mart_funds` は `fund_code` で結合できます。ファンドの発行者（運用会社）は `edinet_code` で `mart_companies` と結合できます
- 財務データのテーブルは `doc_id` で `mart_documents` と結合できます
- `corporate_number`（提出者の法人番号）で houjin_bangou・gbizinfo と結合できます

## 使い方

2 社の売上高の推移を比べる:

```sql
SELECT filer_name, fiscal_year, net_sales
FROM edinet.main.mart_business_results
WHERE sec_code IN ('27530', '79740') AND year_offset = 0
ORDER BY filer_name, fiscal_year;
```

市区町村ごとに、本店のある上場会社を数える:

```sql
SELECT h.lg_code, h.prefecture_name, h.city_name, count(*) AS companies
FROM edinet.main.mart_companies AS c
JOIN houjin_bangou.main.mart_houjin_bangou AS h USING (corporate_number)
WHERE c.listing_status = '上場'
GROUP BY ALL
ORDER BY companies DESC;
```

全社の売上高を有報の連結・当期の総額で並べる（セグメントの内訳を除くため `context_id` で絞ります）:

```sql
SELECT edinet_code, filer_name, period_end, numeric_value AS net_sales
FROM edinet.main.stg_financial_facts
WHERE doc_type_code = '120'
  AND element_id = 'jppfs_cor:NetSales'
  AND context_id = 'CurrentYearDuration'
ORDER BY numeric_value DESC
LIMIT 20;
```

業種ごとに、最新の有報の平均年間給与と男女の賃金の差異を比べる:

```sql
WITH latest AS (
  SELECT *
  FROM edinet.main.mart_human_capital
  QUALIFY row_number() OVER (PARTITION BY edinet_code ORDER BY period_end DESC, doc_id DESC) = 1
)
SELECT c.industry,
       count(*) AS companies,
       median(l.average_annual_salary) AS median_salary,
       median(l.female_to_male_wage_ratio) AS median_wage_ratio
FROM latest AS l
JOIN edinet.main.mart_companies AS c USING (edinet_code)
GROUP BY c.industry
ORDER BY median_salary DESC;
```

ある会社の最新の有報に載っている連結子会社を、議決権の所有割合の高い順に並べる:

```sql
WITH latest AS (
  SELECT doc_id
  FROM edinet.main.mart_documents
  WHERE sec_code = '72030' AND doc_type_code = '120' AND NOT is_withdrawn
  ORDER BY submit_date DESC
  LIMIT 1
)
SELECT name, address, capital_amount, capital_unit, business, voting_rights_ratio
FROM edinet.main.mart_affiliated_entities
JOIN latest USING (doc_id)
WHERE category_group = '連結子会社'
ORDER BY voting_rights_ratio DESC;
```

## 収録範囲の注意

- 財務データは有価証券報告書と半期報告書が対象です（`stg_financial_facts` の `doc_type_code` が 120 / 160）。`mart_business_results` と `mart_human_capital` は有価証券報告書だけから作っています。四半期報告書（2024 年 3 月で廃止）は対象外です
- 提出時点の本体だけで、訂正報告書による訂正は反映していません。過去の書類は新しい提出日から順に取り込んでいる途中で、全期間はまだそろっていません
- 書類の本文（PDF）や添付書類は含みません
- EDINET の閲覧可能期間は書類の種類ごとに最長 10 年（縦覧期間と延長期間）で、それを過ぎた書類は取得できません
- 企業マスタ・ファンドマスタは毎回全件を取得し直した最新の状態です

## 財務データの注意

- `stg_financial_facts` は原典に忠実な縦持ちで、財務諸表（jppfs）・企業情報（jpcrp）・主要経営指標（jpcrp の Summary 系）をすべて収録しています。会社をまたいで比べるときは要素 ID（`element_id`、XBRL の標準タクソノミ。`jppfs_cor:NetSales` など）を使ってください
- `context_id` は期間・連結区分・セグメントを表します。個別は `_NonConsolidatedMember` が付き、年度は `CurrentYear`・`PriorNYear` で分かれます
- 半期報告書の値は中間期のものですが、`period_end` は中間期末ではなく事業年度末です。年度の値と混ぜないでください
- `mart_business_results` は、連結の値があれば連結、無ければ個別の値です。IFRS 採用企業の値（RevenueIFRS など）は JP GAAP の列に統合しています。1 書類あたり当期から四期前までの最大 5 会計年度分の行があるので、同じ会計年度が複数の書類に出てきます。各年の有報の当期の値だけを使うときは `year_offset = 0` で絞ってください
- `mart_affiliated_entities` は XBRL 本体（type=1）の「関係会社の状況」の表を列の見出しで読んで作っています。有報に載っている関係会社だけで、大きな会社は主要な関係会社だけを載せています（「上記以外の連結子会社数は 208 社」など）。名称に法人番号は付いていません。開示府令の様式の有報だけが対象で、投資信託・投資法人の有報は含みません。過去の書類は新しい提出日から順に取り込んでいる途中です
- 1 株当たり純資産は、IFRS 採用企業では 1 株当たり親会社所有者帰属持分です。外貨建て（USD）で開示された値は含みません。自己資本比率は 0〜1 の比率で、債務超過なら負の値です

## 従業員・女性活躍の指標の注意

- 値はその有報の当期分だけです。年をまたぐ推移は各年の有報の行を並べて確認してください。`fiscal_year` は `period_end` の年で、`mart_business_results` の `year_offset = 0` の行と同じ数え方です
- 女性活躍・育児休業の指標は 2024 年以降に提出された有報から数値で取れます。2023 年提出分は本文の文章にしか無く、このテーブルでは空です。平均年齢・平均勤続年数・平均年間給与と女性役員は 2019 年提出分からです
- 平均年間給与は提出会社の値で、賞与と基準外賃金を含みます
- 男性の育児休業取得率は、前年度以前に生まれた子の分も数えるため 1 を超えることがあります。算出の根拠（育児・介護休業法施行規則 71 条の 4 第 1 号・第 2 号、女性活躍推進法）が違う値どうしは定義が違います（`male_childcare_leave_rate_basis`）
- 女性役員の比率は有報の提出日時点の値で、無い書類は当期末時点の値です

有報の開示どおりに取り込んだうえで、次のように整えています。

- 男女の賃金の差異に % のまま入力された値（71.4 など）は 100 で割って比率に直しています（2 を超える値が対象）
- 女性管理職比率と女性役員の比率は、1 を超える値を % とみなして 100 で割っています
- 平均年間給与が 10 万円未満か 5,000 万円を超える値は、桁の入力誤りとみなして NULL にしています。平均臨時雇用人員の負の値も NULL です
- 平均年齢・平均勤続年数を年と月に分けて開示している会社は、年に換算して足しています
- 連結子会社ごとの指標は、数値に子会社名が付いていないので含めていません

## 企業マスタの注意

- `address` は市区町村名から始まり、都道府県名は付きません。本店の市区町村コードは、法人番号で houjin_bangou（国税庁 法人番号公表サイト）と結合すると取れます。上場会社はほぼすべて（2026 年 10 月時点で 3,808 社中 3,805 社）が見つかります
- houjin_bangou は登記上の本店の最新の所在地なので、EDINET の `address` と市区町村が違う会社が数十社あります（移転や、実際の本社と登記上の本店が違う場合）

## 更新

毎日 EDINET の書類一覧 API を日付単位で取得します。未取得の日だけを取得し（進捗は `_source.fetch_progress` に記録）、書類情報の修正・取下げを取り込むため直近 7 日分は毎回取得し直します。

1 回の実行で取得する日数には上限（`EDINET_MAX_DATES_PER_RUN`、既定 1000）があります。全期間を 1 回で取得すると CI のジョブの時間制限を超えるため、毎回ビルドと公開まで完了させ、進捗を `fetch_progress` に残します。次回の実行がその進捗を引き継ぐので、日次の実行で数日かけて履歴（新しい日付から順）が埋まります。遡って取得する開始日は環境変数 `EDINET_BACKFILL_START`（既定 `2016-01-01`）で変えられます。

財務データは、書類一覧で csvFlag=1 の有価証券報告書・半期報告書を、書類取得 API（type=5 の CSV）から書類ごとに取得します。取得済みの書類は `_source.financial_fetch_progress` に記録し、CSV が無い書類は status='empty' として取得し直しません。1 回の実行の書類数の上限は `EDINET_MAX_DOCS_PER_RUN`（既定 1000）、対象の書類の種類は `EDINET_FINANCIAL_DOC_TYPES`（既定 `120,160`。四半期 140・訂正有報 130 などをカンマ区切りで追加できます）で変えられます。

```bash
git submodule update --init
uv sync
bash scripts/build.sh
```
