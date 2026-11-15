{{ config(materialized='view') }}

-- 会社独自の要素の項目名。日本語の冗長ラベルを優先し、無ければ標準ラベルを使う
select doc_id, element_id, label
from {{ ref('raw_xbrl_labels') }}
where lang = 'ja'
    and role in ('http://www.xbrl.org/2003/role/verboseLabel', 'http://www.xbrl.org/2003/role/label')
qualify row_number() over (
    partition by doc_id, element_id
    order by role = 'http://www.xbrl.org/2003/role/verboseLabel' desc, label
) = 1
