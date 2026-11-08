-- 直近 1 年の書類が使った標準タクソノミの名前空間が、取り込んだタクソノミにあること。
-- 年版の途中の改訂など、取り込みの対象から漏れた公表を見つける
with used as (
    select distinct
        n.doc_id,
        regexp_extract(n.uri, '/taxonomy/([^/]+/[0-9]{4}-[0-9]{2}-[0-9]{2})/', 1) as namespace
    from {{ ref('raw_xbrl_namespaces') }} as n
    join {{ ref('stg_documents') }} as d on n.doc_id = d.doc_id
    where n.uri like 'http://disclosure.edinet-fsa.go.jp/taxonomy/%'
        and d.submit_date >= current_date - interval 365 day
)

select namespace, count(distinct doc_id) as docs
from used
where namespace not in (select distinct namespace from {{ ref('raw_taxonomy_labels') }})
group by namespace
