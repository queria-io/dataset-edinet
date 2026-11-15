{{ config(materialized='view') }}

-- XBRL インスタンスの context。期間の日付を型付けし、時点か期間か、個別のメンバーを持つかを取り出す
select
    doc_id,
    context_id,
    instant is not null as is_instant,
    members like '%NonConsolidatedMember%' as is_non_consolidated,
    try_cast(period_start as date) as context_start,
    try_cast(coalesce(period_end, instant) as date) as context_end
from {{ ref('raw_xbrl_contexts') }}
