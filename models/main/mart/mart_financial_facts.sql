{{ config(alias='stg_financial_facts', materialized='view') }}

-- 公開しているテーブルの名前 stg_financial_facts を保つため alias を付ける。
-- 行が 1 億を超え、実体化すると raw と同じ大きさになるので、今までどおり view にする

-- 有報・半期報告書の財務の値を、1 要素 × 1 コンテキスト 1 行の縦持ちで持つ。
-- 元は書類取得 API type=1 の XBRL インスタンス。EDINET が type=1 を配らなくなった過去の書類だけ、
-- 以前に取った type=5 の CSV（EDINET がインスタンスを変換したもの）の行を使う。
-- 相対年度・連結/個別・期間/時点・単位は、CSV と同じ値になるよう context から作る。
with docs as (
    -- documents は同一 doc_id が複数の取得日に現れることがある（一覧の再掲）。
    -- 結合でファクトが増殖しないよう doc_id 単位に 1 行へ畳む。
    select *
    from {{ ref('stg_documents') }}
    qualify row_number() over (partition by doc_id order by submit_datetime desc) = 1
),

-- XBRL のインスタンスを読めた書類。値の表（テキストブロックの HTML を含む）を全件見ずに済むよう、
-- 同じ書類について必ず行のある context の表から取る
xbrl_docs as (
    select distinct doc_id from {{ ref('stg_xbrl_contexts') }}
),

-- 標準の要素の項目名は stg_taxonomy_item_names（ビルドのたびに 1 回だけ作る表）から引く
taxonomy_names as (
    select namespace, element_id, label
    from {{ ref('stg_taxonomy_item_names') }}
    where namespace <> '*'
),

latest_names as (
    select element_id, label
    from {{ ref('stg_taxonomy_item_names') }}
    where namespace = '*'
),

xbrl as (
    select
        f.doc_id,
        f.element_id,
        coalesce(cn.label, tn.label, ln.label) as item_name,
        f.context_id,
        ry.relative_year,
        -- CSV と同じく、日本基準の財務諸表 (jppfs_cor) だけを連結 / 個別に分ける
        case
            when f.element_id not like 'jppfs\_cor:%' escape '\' then 'その他'
            when c.is_non_consolidated then '個別'
            else '連結'
        end as consolidated_individual,
        case when c.is_instant then '時点' else '期間' end as period_instant,
        f.unit_id,
        f.unit,
        f.value,
        f.decimals,
        f.is_nil,
        c.context_start,
        c.context_end,
        null::varchar as csv_type,
        f.row_seq,
        'xbrl' as source
    from {{ ref('stg_xbrl_facts') }} as f
    left join {{ ref('stg_xbrl_contexts') }} as c
        on f.doc_id = c.doc_id and f.context_id = c.context_id
    left join {{ ref('stg_xbrl_labels') }} as cn
        on f.doc_id = cn.doc_id and f.element_id = cn.element_id
    left join {{ ref('stg_xbrl_namespaces') }} as ns
        on f.doc_id = ns.doc_id and f.prefix = ns.prefix
    left join taxonomy_names as tn
        on ns.namespace = tn.namespace and f.element_id = tn.element_id
    left join latest_names as ln
        on f.element_id = ln.element_id
    left join {{ ref('context_relative_years') }} as ry
        on f.context_prefix = ry.context_prefix
),

csv as (
    select *, 'csv' as source
    from {{ ref('stg_csv_facts') }}
    -- 監査報告書 (jpaud) は type=1 では取っていないので、CSV でもそろえて外す
    where csv_type not like 'jpaud%'
        and doc_id not in (select doc_id from xbrl_docs)
),

facts as (
    select * from xbrl
    union all
    select * from csv
)

select
    f.doc_id,
    d.edinet_code,
    d.sec_code,
    d.corporate_number,
    d.filer_name,
    d.doc_type_code,
    d.period_end,
    d.submit_date,
    d.doc_description,
    f.element_id,
    f.item_name,
    f.context_id,
    f.relative_year,
    f.consolidated_individual,
    f.period_instant,
    f.unit_id,
    f.unit,
    f.value,
    try_cast(f.value as decimal(38, 4)) as numeric_value,
    f.decimals,
    f.is_nil,
    f.context_start,
    f.context_end,
    f.csv_type,
    f.row_seq,
    f.source
from facts as f
left join docs as d
    on f.doc_id = d.doc_id
