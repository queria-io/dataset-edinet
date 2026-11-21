---
$schema: https://queria.io/schemas/table/v1.json
table: main.mart_company_financials
title: EDINET 企業の財務指標（決算期ごと）
description: |-
  有価証券報告書の損益計算書・貸借対照表・キャッシュ・フロー計算書の主要な項目と、営業利益率・ROE などの指標を、1 社 1 決算期 1 行にまとめたものです。日本基準・IFRS・米国基準の値を同じ列に統一しています。

  - 連結財務諸表のある会社は連結、無い会社は個別の値です（`is_consolidated`）
  - 各決算期の有報に載った当期の値です。同じ決算期に有報が複数あるときは、最後に提出した書類の値です。訂正報告書による訂正は反映していません
  - 金額の単位は円です。外貨建てで開示された値は含みません
  - IFRS の会社の 2018 年度以前は、連結財務諸表の本体に XBRL の要素が無いため、売上原価・営業利益・負債などが NULL です
  - どの XBRL の要素を何の列にしたかは、リポジトリの `seeds/financial_items.csv` にあります
primary_key: [edinet_code, period_end]
sources:
  - path: https://disclosure2.edinet-fsa.go.jp/
fields:
  - name: edinet_code
    title: 提出者EDINETコード
    description: mart_companies と結合でき、業種や上場区分を引ける
  - name: sec_code
    title: 提出者証券コード
  - name: corporate_number
    title: 提出者法人番号
    description: 13桁。houjin_bangou / gbizinfo と結合できる
    semantic: {role: entity, name: corporate_number}
  - name: filer_name
    title: 提出者名
  - name: fiscal_year
    title: 会計年度
    description: period_end の年。mart_business_results の year_offset = 0 の行と同じ数え方
  - name: period_start
    title: 当期会計期間（自）
  - name: period_end
    title: 当期会計期間（至）
  - name: accounting_standard
    title: 会計基準
    description: Japan GAAP・IFRS・US GAAP。XBRL を取得できず CSV の値だけの書類は NULL
  - name: is_consolidated
    title: 連結の値か
    description: true なら連結、false なら個別（連結財務諸表を作っていない会社）
  - name: revenue
    title: 売上高・営業収益
    description: 主要な経営指標等の推移の売上高。営業収益・営業総収入で開示する会社はその値、銀行・保険会社は経常収益。IFRS は売上収益。IFRS の一部の会社（トヨタ自動車など）は会社独自の要素で開示しているため NULL
  - name: cost_of_sales
    title: 売上原価
  - name: gross_profit
    title: 売上総利益
  - name: sga_expenses
    title: 販売費及び一般管理費
  - name: operating_income
    title: 営業利益
    description: IFRS で営業利益を表示しない会社、米国基準で主要な経営指標に営業利益の無い会社、銀行・保険会社は NULL
  - name: ordinary_income
    title: 経常利益
    description: 日本基準だけ。IFRS・米国基準は NULL
  - name: profit_before_tax
    title: 税引前当期純利益
  - name: net_income
    title: 親会社株主に帰属する当期純利益
    description: 個別の値の会社は当期純利益（IFRS は当期利益）。IFRS・米国基準は親会社の所有者に帰属する当期利益
  - name: total_assets
    title: 総資産
  - name: current_assets
    title: 流動資産
  - name: total_liabilities
    title: 負債合計
  - name: current_liabilities
    title: 流動負債
  - name: net_assets
    title: 純資産
    description: IFRS は資本合計、米国基準は非支配持分を含む純資産
  - name: owners_equity
    title: 自己資本
    description: 日本基準は純資産から新株予約権と非支配株主持分を引いた額。IFRS・米国基準は親会社の所有者に帰属する持分。IFRS で個別の値の会社は資本合計
  - name: cash_and_equivalents
    title: 現金及び現金同等物の期末残高
  - name: operating_cash_flow
    title: 営業活動によるキャッシュ・フロー
  - name: investing_cash_flow
    title: 投資活動によるキャッシュ・フロー
  - name: financing_cash_flow
    title: 財務活動によるキャッシュ・フロー
  - name: capital_expenditure
    title: 有形固定資産の取得による支出
    description: キャッシュ・フロー計算書の値で、支出は負の値
  - name: depreciation
    title: 減価償却費
    description: 営業活動によるキャッシュ・フローの調整項目の値
  - name: research_and_development_expenses
    title: 研究開発費
    description: 有報の「研究開発活動」に記載された研究開発費の総額
  - name: number_of_employees
    title: 従業員数
    description: 連結の値の会社は連結、個別の値の会社は提出会社の当期末の人数
  - name: total_issued_shares
    title: 発行済株式総数
    description: 提出会社の当期末の値
  - name: basic_eps
    title: 1株当たり当期純利益（EPS）
  - name: bps
    title: 1株当たり純資産（BPS）
    description: IFRS は 1 株当たり親会社所有者帰属持分、米国基準は 1 株当たり株主資本
  - name: dividend_per_share
    title: 1株当たり配当額
    description: 提出会社の当期の年間配当額
  - name: operating_margin
    title: 営業利益率
    description: operating_income / revenue
  - name: roe
    title: 自己資本利益率（ROE）
    description: net_income を期首と期末の owners_equity の平均で割った値。期首の値は同じ有報に載った前期末の値
  - name: roa
    title: 総資産利益率（ROA）
    description: net_income を期首と期末の total_assets の平均で割った値
  - name: equity_ratio
    title: 自己資本比率
    description: owners_equity / total_assets。債務超過なら負の値
  - name: revenue_growth
    title: 売上高の前期比
    description: revenue を同じ有報に載った前期の値で割って 1 を引いた値（0.05 なら 5% 増）。決算期を変更した年は月数の違う期間どうしの比
  - name: doc_id
    title: 書類管理番号
    description: 値を取った有報。mart_documents / stg_financial_facts と結合できる
  - name: submit_date
    title: 提出日
  - name: source_url
    title: 出典 URL
    description: EDINET の書類の閲覧ページ
---
