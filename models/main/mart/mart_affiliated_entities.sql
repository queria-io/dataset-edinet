{{ config(materialized='table') }}

-- 有報の「関係会社の状況」を 1 社 1 行にした表。表の読み取りは main.py（edinet/affiliates.py）で
-- 行い、ここでは注記番号・資本金の単位・議決権の内数を分ける。
with docs as (
    select *
    from {{ ref('stg_documents') }}
    qualify row_number() over (partition by doc_id order by submit_datetime desc) = 1
),

-- 「〃」「同上」は直前の会社と同じ値なので、書類の中で前の行の値を写す。空のセルは空のまま
filled as (
    select
        * replace (
            {% for col in ['address', 'capital', 'business', 'relationship'] -%}
            case
                when trim({{ col }}) in ('〃', '同上') then last_value(
                    case when trim({{ col }}) not in ('〃', '同上') then {{ col }} end ignore nulls
                ) over (partition by doc_id order by row_seq rows between unbounded preceding and 1 preceding)
                else {{ col }}
            end as {{ col }}{{ "," if not loop.last }}
            {% endfor %}
        )
    from {{ ref('raw_affiliated_entities') }}
),

src as (
    select
        a.*,
        -- 全角の数字・記号を半角にしてから読む
        translate(a.capital, '０１２３４５６７８９，．', '0123456789,.') as capital_ascii,
        -- 2 段の見出しの「所有割合（％）: 62.5 / 被所有割合（％）: －」は見出しを外す
        regexp_replace(
            translate(a.voting_rights, '０１２３４５６７８９．（）', '0123456789.()'),
            '[^/:]*:\s*', '', 'g'
        ) as voting_ascii,
        case
            when a.category like '親会社%' then '親会社'
            when a.category like 'その他の関係会社%' then 'その他の関係会社'
            when a.category like '持分法適用%' or a.category like '関連会社%' then '持分法適用会社・関連会社'
            when a.category like '非連結子会社%' then '非連結子会社'
            when a.category like '連結子会社%' then '連結子会社'
        end as category_group
    from filled as a
),

parsed as (
    select
        *,
        -- 名称の末尾の「（注）３」「（注）２、４」「※１」を外し、折り返しで入った和文どうしの間の
        -- 空白を詰める（「株式会社 ツーアール」→「株式会社ツーアール」。英文の空白は残す）。
        -- 重なる一致を拾えないので 2 回かける
        regexp_replace(
            regexp_replace(
                trim(regexp_replace(name, '\s*(（注[）)]|\(注\)|※)\s*[0-9０-９,、，・.．\s]*', '', 'g')),
                '([^\x00-\x7F])\s+([^\x00-\x7F])', '\1\2', 'g'
            ),
            '([^\x00-\x7F])\s+([^\x00-\x7F])', '\1\2', 'g'
        ) as name_clean,
        nullif(array_to_string(regexp_extract_all(name, '(?:（注[）)]|\(注\)|※)\s*([0-9０-９,、，・\s]+)', 1), ','), '') as note_refs,
        try_cast(replace(regexp_extract(capital_ascii, '[0-9][0-9,]*(\.[0-9]+)?'), ',', '') as decimal(24, 4)) as capital_amount,
        -- 単位はセルの数字以外の文字。無ければ見出しの括弧書き（「資本金（百万円）」）
        coalesce(
            nullif(trim(regexp_replace(capital_ascii, '[0-9,.\s]|（注[）)]?|\(注\)|※', '', 'g')), ''),
            nullif(regexp_extract(capital_header, '[（(]([^）)]+)[）)]', 1), '')
        ) as capital_unit,
        try_cast(regexp_extract(voting_ascii, '([0-9]+(\.[0-9]+)?)', 1) as decimal(7, 2)) as voting_pct,
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
    p.category_group,
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
    -- 0〜1 の比率。voting_rights_held_by_affiliate の行は、提出会社が所有される割合
    p.voting_pct / 100 as voting_rights_ratio,
    p.voting_indirect_pct / 100 as voting_rights_indirect_ratio,
    -- 割合が「被所有」（相手が提出会社の議決権を持つ）か。親会社・その他の関係会社の行と、
    -- 値が「被所有」で始まる行
    coalesce(p.category_group in ('親会社', 'その他の関係会社'), false)
        or trim(p.voting_ascii) like '被所有%' as voting_rights_held_by_affiliate,
    p.relationship
from parsed as p
left join docs as d on p.doc_id = d.doc_id
