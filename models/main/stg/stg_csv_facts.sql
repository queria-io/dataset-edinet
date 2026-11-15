{{ config(materialized='view') }}

-- type=5 の CSV（EDINET が XBRL インスタンスを変換したもの）の財務の値を、XBRL の行と同じ列にそろえる
select
    doc_id,
    element_id,
    item_name,
    context_id,
    relative_year,
    consolidated_individual,
    period_instant,
    unit_id,
    unit,
    value,
    null::varchar as decimals,
    value = '－' as is_nil,
    null::date as context_start,
    null::date as context_end,
    _csv_type as csv_type,
    _row_seq as row_seq
from {{ ref('raw_financial_facts') }}
