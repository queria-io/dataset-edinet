{{ config(materialized='table') }}

-- 有報の「関係会社の状況」を 1 社 1 行にした表。行の読み取りと型付けは stg_affiliated_entities
with docs as (
    select *
    from {{ ref('stg_documents') }}
    qualify row_number() over (partition by doc_id order by submit_datetime desc) = 1
)

select
    a.doc_id,
    d.edinet_code,
    d.sec_code,
    d.corporate_number,
    d.filer_name,
    d.period_end,
    year(d.period_end) as fiscal_year,
    d.submit_date,
    a.* exclude (doc_id)
from {{ ref('stg_affiliated_entities') }} as a
left join docs as d on a.doc_id = d.doc_id
