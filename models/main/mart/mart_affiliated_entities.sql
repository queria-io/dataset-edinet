{{ config(materialized='table') }}

-- 有報の「関係会社の状況」を 1 社 1 行にした表。表の読み取りは main.py（edinet/affiliates.py）で
-- 行い、ここでは注記番号・資本金の単位・議決権の内数を分ける。
with docs as (
    select *
    from {{ ref('stg_documents') }}
    qualify row_number() over (partition by doc_id order by submit_datetime desc) = 1
),

src as (
    select
        a.*,
        -- 全角の数字・記号を半角にしてから読む
        translate(a.capital, '０１２３４５６７８９，．', '0123456789,.') as capital_ascii,
        translate(a.voting_rights, '０１２３４５６７８９．（）', '0123456789.()') as voting_ascii
    from {{ ref('raw_affiliated_entities') }} as a
),

parsed as (
    select
        *,
        -- 名称の末尾の「（注）３」「（注）２、４」「※１」を外す
        trim(regexp_replace(name, '\s*(（注[）)]|\(注\)|※)\s*[0-9０-９,、，・\s]*', '', 'g')) as name_clean,
        nullif(array_to_string(regexp_extract_all(name, '(?:（注[）)]|\(注\)|※)\s*([0-9０-９,、，・\s]+)', 1), ','), '') as note_refs,
        try_cast(replace(regexp_extract(capital_ascii, '[0-9][0-9,]*(\.[0-9]+)?'), ',', '') as decimal(24, 4)) as capital_amount,
        -- 単位はセルの数字以外の文字。無ければ見出しの括弧書き（「資本金（百万円）」）
        coalesce(
            nullif(trim(regexp_replace(capital_ascii, '[0-9,.\s]|（注[）)]?|\(注\)|※', '', 'g')), ''),
            nullif(regexp_extract(capital_header, '[（(]([^）)]+)[）)]', 1), '')
        ) as capital_unit,
        try_cast(regexp_extract(voting_ascii, '^\s*([0-9]+(\.[0-9]+)?)', 1) as decimal(7, 2)) as voting_pct,
        try_cast(regexp_extract(voting_ascii, '\(\s*([0-9]+(\.[0-9]+)?)\s*\)', 1) as decimal(7, 2)) as voting_indirect_pct
    from src
)

select
    p.doc_id,
    d.edinet_code,
    d.sec_code,
    d.corporate_number,
    d.filer_name,
    d.period_end,
    year(d.period_end) as fiscal_year,
    d.submit_date,
    p.row_seq,
    p.category,
    p.name_clean as name,
    p.note_refs,
    p.address,
    p.capital as capital_text,
    p.capital_amount,
    p.capital_unit,
    -- 円建ての単位だけ円に直す。外貨は capital_amount と capital_unit を見る
    case p.capital_unit
        when '円' then p.capital_amount
        when '千円' then p.capital_amount * 1000
        when '百万円' then p.capital_amount * 1000000
        when '億円' then p.capital_amount * 100000000
    end::bigint as capital_yen,
    p.business,
    p.voting_rights as voting_rights_text,
    -- 0〜1 の比率。被所有（親会社・その他の関係会社）の行は、提出会社が所有される割合
    p.voting_pct / 100 as voting_rights_ratio,
    p.voting_indirect_pct / 100 as voting_rights_indirect_ratio,
    p.relationship
from parsed as p
left join docs as d on p.doc_id = d.doc_id
