-- XBRL の行の context ID の頭が、相対年度の対応テーブルにあること（命名規約の追加を見つける）。
-- CSV でも相対年度が空の頭（RecordDateInstant など）は、対応テーブルに空の値で入れてある。
-- stg_financial_facts の XBRL の行は raw_xbrl_facts と 1 対 1 なので、項目名の結合を通さずに元を読む
select split_part(f.context_id, '_', 1) as prefix, count(*) as n
from {{ ref('raw_xbrl_facts') }} as f
left join {{ ref('context_relative_years') }} as ry
    on split_part(f.context_id, '_', 1) = ry.context_prefix
where ry.context_prefix is null
group by prefix
