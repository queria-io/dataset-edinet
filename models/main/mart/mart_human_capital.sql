{{ config(materialized='table') }}

-- 有報の「従業員の状況」「提出会社の指標（女性活躍・育児休業）」「役員の状況」を
-- 1 書類 = 1 行に横持ちにした表。当期の値だけを持つ（過去分は前年の有報の行で追う）。
--
-- context_id の扱い:
--   - 従業員数・臨時雇用人員は CurrentYearInstant が連結、
--     CurrentYearInstant_NonConsolidatedMember が提出会社。セグメント別の行
--     （…ReportableSegment(s)Member・CorporateSharedMember）は使わない
--   - 提出会社の指標は会社ごとに context の付け方が揺れる（NonConsolidatedMember /
--     接尾辞なし / Row1Member）。この順に優先して 1 つ選ぶ。
--     連結子会社の指標（…MetricsOfConsolidatedSubsidiaries）は子会社ごとの行で、
--     数値ファクトに子会社名が無いのでここでは扱わない
--   - 女性役員は FilingDateInstant（提出日時点）
-- 同じ element_id × context_id の行が書類内で重複して出ることがある（値は同じ）ので max で潰す。
with facts as (
    select
        doc_id,
        edinet_code,
        sec_code,
        corporate_number,
        filer_name,
        period_end,
        submit_date,
        element_id,
        context_id,
        numeric_value
    from {{ ref('stg_financial_facts') }}
    where numeric_value is not null
      and (
          element_id in (
              'jpcrp_cor:NumberOfEmployees',
              'jpcrp_cor:AverageNumberOfTemporaryWorkers',
              'jpcrp_cor:NumberOfFemaleDirectorsAndOtherOfficers',
              'jpcrp_cor:RatioOfFemaleDirectorsAndOtherOfficers'
          )
          or element_id like 'jpcrp\_cor:%InformationAboutReportingCompanyInformationAboutEmployees' escape '\'
          or element_id like 'jpcrp\_cor:%MetricsOfReportingCompany' escape '\'
      )
),

-- 提出会社の指標で使う context の優先順位（小さいほど優先）
ranked as (
    select
        *,
        case context_id
            when 'CurrentYearInstant_NonConsolidatedMember' then 1
            when 'CurrentYearInstant' then 2
            when 'CurrentYearInstant_Row1Member' then 3
        end as context_rank
    from facts
),

metrics as (
    select
        doc_id,
        element_id,
        arg_min(numeric_value, context_rank) as numeric_value
    from ranked
    where element_id like '%MetricsOfReportingCompany'
      and context_rank is not null
    group by doc_id, element_id
),

-- 育児休業取得率は算出の根拠（育児・介護休業法施行規則 71 条の 4 第 1 号 / 第 2 号、
-- 女性活躍推進法）ごとに別の要素になる。会社はどれか 1 つで開示するので、
-- 全労働者の値を第 1 号 → 第 2 号 → 女性活躍推進法の順に 1 つ選び、根拠を別列に残す
childcare as (
    select
        doc_id,
        arg_min(numeric_value, basis_rank) as male_childcare_leave_rate,
        arg_min(basis, basis_rank) as male_childcare_leave_rate_basis
    from (
        select
            doc_id,
            numeric_value,
            case
                when element_id like '%Article714Item1Of%' then 1
                when element_id like '%Article714Item2Of%' then 2
                when element_id like '%ActOnPromotionOfWomensActiveEngagement%' then 3
            end as basis_rank,
            case
                when element_id like '%Article714Item1Of%' then '育児・介護休業法施行規則71条の4第1号'
                when element_id like '%Article714Item2Of%' then '育児・介護休業法施行規則71条の4第2号'
                when element_id like '%ActOnPromotionOfWomensActiveEngagement%' then '女性活躍推進法'
            end as basis
        from metrics
        where element_id like 'jpcrp\_cor:AllEmployees%RatioOfMaleEmployeesTakingChildcareLeaveMetricsOfReportingCompany' escape '\'
    )
    where basis_rank is not null
    group by doc_id
),

-- 割合の列には % のまま入力された値（71.4 など）が混じる。賃金の差異と女性管理職比率は
-- 定義上 1 を大きく超えないので、2 を超える値は % とみなして 100 で割る
pivoted as (
    select
        f.doc_id,
        any_value(f.edinet_code) as edinet_code,
        any_value(f.sec_code) as sec_code,
        any_value(f.corporate_number) as corporate_number,
        any_value(f.filer_name) as filer_name,
        any_value(f.period_end) as period_end,
        any_value(f.submit_date) as submit_date,
        max(case when f.element_id = 'jpcrp_cor:NumberOfEmployees' and f.context_id = 'CurrentYearInstant' then f.numeric_value end) as number_of_employees_consolidated,
        max(case when f.element_id = 'jpcrp_cor:NumberOfEmployees' and f.context_id = 'CurrentYearInstant_NonConsolidatedMember' then f.numeric_value end) as number_of_employees,
        max(case when f.element_id = 'jpcrp_cor:AverageNumberOfTemporaryWorkers' and f.context_id = 'CurrentYearInstant' then f.numeric_value end) as temporary_workers_consolidated,
        max(case when f.element_id = 'jpcrp_cor:AverageNumberOfTemporaryWorkers' and f.context_id = 'CurrentYearInstant_NonConsolidatedMember' then f.numeric_value end) as temporary_workers,
        max(case when f.element_id = 'jpcrp_cor:AverageAgeYearsInformationAboutReportingCompanyInformationAboutEmployees' then f.numeric_value end) as average_age_years,
        max(case when f.element_id = 'jpcrp_cor:AverageAgeMonthsInformationAboutReportingCompanyInformationAboutEmployees' then f.numeric_value end) as average_age_months,
        max(case when f.element_id = 'jpcrp_cor:AverageLengthOfServiceYearsInformationAboutReportingCompanyInformationAboutEmployees' then f.numeric_value end) as length_of_service_years,
        max(case when f.element_id = 'jpcrp_cor:AverageLengthOfServiceMonthsInformationAboutReportingCompanyInformationAboutEmployees' then f.numeric_value end) as length_of_service_months,
        max(case when f.element_id = 'jpcrp_cor:AverageAnnualSalaryInformationAboutReportingCompanyInformationAboutEmployees' then f.numeric_value end) as average_annual_salary,
        max(case when f.element_id = 'jpcrp_cor:NumberOfFemaleDirectorsAndOtherOfficers' and f.context_id = 'FilingDateInstant' then f.numeric_value end) as female_officers,
        max(case when f.element_id = 'jpcrp_cor:RatioOfFemaleDirectorsAndOtherOfficers' and f.context_id = 'FilingDateInstant' then f.numeric_value end) as female_officer_ratio
    from facts as f
    group by f.doc_id
),

reporting_metrics as (
    select
        doc_id,
        max(case when element_id = 'jpcrp_cor:RatioOfFemaleEmployeesInManagerialPositionsMetricsOfReportingCompany' then numeric_value end) as female_manager_ratio,
        max(case when element_id = 'jpcrp_cor:AllEmployeesDifferencesInWagesBetweenMaleAndFemaleEmployeesMetricsOfReportingCompany' then numeric_value end) as female_to_male_wage_ratio,
        max(case when element_id = 'jpcrp_cor:RegularEmployeesDifferencesInWagesBetweenMaleAndFemaleEmployeesMetricsOfReportingCompany' then numeric_value end) as female_to_male_wage_ratio_regular,
        max(case when element_id = 'jpcrp_cor:NonRegularEmployeesDifferencesInWagesBetweenMaleAndFemaleEmployeesMetricsOfReportingCompany' then numeric_value end) as female_to_male_wage_ratio_non_regular
    from metrics
    group by doc_id
)

select
    p.doc_id,
    p.edinet_code,
    p.sec_code,
    p.corporate_number,
    p.filer_name,
    p.period_end,
    year(p.period_end) as fiscal_year,
    p.submit_date,
    p.number_of_employees_consolidated::bigint as number_of_employees_consolidated,
    p.number_of_employees::bigint as number_of_employees,
    p.temporary_workers_consolidated::bigint as temporary_workers_consolidated,
    p.temporary_workers::bigint as temporary_workers,
    -- 年と月で別の要素に分けて開示する会社がある（42 年 6 月 → 42.5）
    round(p.average_age_years + coalesce(p.average_age_months, 0) / 12, 1) as average_age,
    round(p.length_of_service_years + coalesce(p.length_of_service_months, 0) / 12, 1) as average_length_of_service,
    -- 1 億円を超える平均年間給与は単位の入力誤り（千円単位の値を円で入れたもの等）とみなして落とす
    case when p.average_annual_salary < 100000000 then p.average_annual_salary end as average_annual_salary,
    case when m.female_manager_ratio > 2 then m.female_manager_ratio / 100 else m.female_manager_ratio end as female_manager_ratio,
    case when m.female_to_male_wage_ratio > 2 then m.female_to_male_wage_ratio / 100 else m.female_to_male_wage_ratio end as female_to_male_wage_ratio,
    case when m.female_to_male_wage_ratio_regular > 2 then m.female_to_male_wage_ratio_regular / 100 else m.female_to_male_wage_ratio_regular end as female_to_male_wage_ratio_regular,
    case when m.female_to_male_wage_ratio_non_regular > 2 then m.female_to_male_wage_ratio_non_regular / 100 else m.female_to_male_wage_ratio_non_regular end as female_to_male_wage_ratio_non_regular,
    c.male_childcare_leave_rate,
    c.male_childcare_leave_rate_basis,
    p.female_officers::integer as female_officers,
    p.female_officer_ratio
from pivoted as p
left join reporting_metrics as m using (doc_id)
left join childcare as c using (doc_id)
-- 人的資本の項目が 1 つも無い書類（投資法人など）は落とす
where coalesce(
    p.number_of_employees_consolidated, p.number_of_employees, p.average_annual_salary,
    m.female_manager_ratio, m.female_to_male_wage_ratio, c.male_childcare_leave_rate, p.female_officer_ratio
) is not null
