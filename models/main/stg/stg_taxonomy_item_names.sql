{{ config(materialized='table') }}

-- 標準の要素の項目名の引き当て表。stg_financial_facts は view なので、ここで 1 回だけ並べ替える。
-- namespace が '*' の行は、書類と同じ名前空間のラベルが無いときに使う最も新しい年版のラベル
-- 項目名は CSV と同じく冗長ラベル（「売上高、営業収益」など）を使い、無ければ標準ラベル。
-- 標準の要素は、書類の名前空間と同じモジュール・日付のタクソノミのラベルを当てる。
-- 同じ名前空間のラベルが複数の年版・ファイルにあるときは、新しい公表・新しいファイルを使う
with label_candidates as (
    select
        namespace,
        element_id,
        label,
        release,
        regexp_extract(file, '([0-9]{4}-[0-9]{2}-[0-9]{2})_lab', 1) as file_date,
        role = 'http://www.xbrl.org/2003/role/verboseLabel' as is_verbose
    from {{ ref('raw_taxonomy_labels') }}
    where lang = 'ja'
        and role in ('http://www.xbrl.org/2003/role/verboseLabel', 'http://www.xbrl.org/2003/role/label')
),

taxonomy_names as (
    select namespace, element_id, label
    from label_candidates
    qualify row_number() over (
        partition by namespace, element_id order by is_verbose desc, release desc, file_date desc
    ) = 1
),

-- 書類と同じ名前空間に無い要素（年版の間で名前空間の日付がずれたもの）は、最も新しい年版から取る
latest_names as (
    select element_id, label
    from label_candidates
    qualify row_number() over (
        partition by element_id order by is_verbose desc, release desc, file_date desc
    ) = 1
)

select namespace, element_id, label from taxonomy_names
union all
select '*' as namespace, element_id, label from latest_names
