{{ config(materialized='view') }}

-- 書類の名前空間の接頭辞と、そのタクソノミのモジュール・日付（stg_taxonomy_item_names の namespace）
select
    doc_id,
    prefix,
    regexp_extract(uri, '/taxonomy/([^/]+/[0-9]{4}-[0-9]{2}-[0-9]{2})/', 1) as namespace
from {{ ref('raw_xbrl_namespaces') }}
