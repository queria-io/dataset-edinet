{{ config(materialized='table') }}

-- 有報の表紙（会社名・英訳名・本店の所在の場所・電話番号・最寄りの連絡場所）と、
-- 連結子会社の数・研究開発費を 1 書類 = 1 行にした表。
-- 表紙は提出日時点（FilingDateInstant）、連結子会社の数は当期末、研究開発費は当期の合計
-- （セグメント別の行は使わない）。
-- 代表者・事務連絡者の氏名は個人の情報なので持たない。
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
        nullif(trim(value), '') as value,
        numeric_value
    from {{ ref('stg_financial_facts') }}
    -- 有報だけ（半期報告書 160 などは混ぜない）
    where doc_type_code = '120'
      and (
          (
              context_id = 'FilingDateInstant'
              and element_id in (
                  'jpcrp_cor:CompanyNameCoverPage',
                  'jpcrp_cor:CompanyNameInEnglishCoverPage',
                  'jpcrp_cor:AddressOfRegisteredHeadquarterCoverPage',
                  'jpcrp_cor:TelephoneNumberAddressOfRegisteredHeadquarterCoverPage',
                  'jpcrp_cor:NearestPlaceOfContactCoverPage',
                  'jpcrp_cor:TelephoneNumberNearestPlaceOfContactCoverPage'
              )
          )
          or (context_id = 'CurrentYearInstant' and element_id = 'jpcrp_cor:NumberOfConsolidatedSubsidiaries')
          or (context_id = 'CurrentYearDuration' and element_id = 'jpcrp_cor:ResearchAndDevelopmentExpensesResearchAndDevelopmentActivities')
      )
),

pivoted as (
    select
        doc_id,
        any_value(edinet_code) as edinet_code,
        any_value(sec_code) as sec_code,
        any_value(corporate_number) as corporate_number,
        any_value(filer_name) as filer_name,
        any_value(period_end) as period_end,
        year(any_value(period_end)) as fiscal_year,
        any_value(submit_date) as submit_date,
        max(case when element_id = 'jpcrp_cor:CompanyNameCoverPage' then value end) as company_name,
        max(case when element_id = 'jpcrp_cor:CompanyNameInEnglishCoverPage' then value end) as company_name_en,
        max(case when element_id = 'jpcrp_cor:AddressOfRegisteredHeadquarterCoverPage' then value end) as head_office_address,
        max(case when element_id = 'jpcrp_cor:TelephoneNumberAddressOfRegisteredHeadquarterCoverPage' then value end) as head_office_phone,
        max(case when element_id = 'jpcrp_cor:NearestPlaceOfContactCoverPage' then value end) as contact_address,
        max(case when element_id = 'jpcrp_cor:TelephoneNumberNearestPlaceOfContactCoverPage' then value end) as contact_phone,
        max(case when element_id = 'jpcrp_cor:NumberOfConsolidatedSubsidiaries' then numeric_value end)::integer as consolidated_subsidiaries,
        max(case when element_id = 'jpcrp_cor:ResearchAndDevelopmentExpensesResearchAndDevelopmentActivities' then numeric_value end)::bigint as research_and_development_expenses
    from facts
    group by doc_id
)

-- 電話番号は「03-1234-5678（代表）」「東京(03)1234－5678」「０８６(２２６)７１１１」など書き方が
-- 統一されていないので、数字だけを抜き出した列を別に持つ。日本の固定・携帯・IP 電話の 10〜11 桁で
-- なければ NULL（番号が 2 つ書かれた値など）
select
    *,
    {{ phone_digits('head_office_phone') }} as head_office_phone_digits,
    {{ phone_digits('contact_phone') }} as contact_phone_digits
from pivoted
