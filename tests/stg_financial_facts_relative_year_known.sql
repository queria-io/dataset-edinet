-- XBRL の行で、context ID の頭から相対年度が決まらないものが無いこと（命名規約の追加を見つける）
select split_part(context_id, '_', 1) as prefix, count(*) as n
from {{ ref('stg_financial_facts') }}
where source = 'xbrl' and relative_year is null
group by prefix
