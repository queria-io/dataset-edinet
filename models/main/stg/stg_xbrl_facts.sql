{{ config(materialized='view') }}

-- XBRL インスタンスの値。単位と nil の値を CSV（type=5）と同じ表記にし、
-- 名前空間の接頭辞と context ID の頭（EDINET の命名規約で相対年度を表す）を取り出す
select
    doc_id,
    element_id,
    split_part(element_id, ':', 1) as prefix,
    context_id,
    split_part(context_id, '_', 1) as context_prefix,
    coalesce(unit_id, '－') as unit_id,
    case when unit_id is null then '－' when unit_id = 'JPY' then '円' end as unit,
    case when is_nil then '－' else value end as value,
    decimals,
    is_nil,
    seq as row_seq
from {{ ref('raw_xbrl_facts') }}
