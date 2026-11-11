{{ config(materialized='view') }}

select
    doc_id,
    sha256,
    bytes,
    'application/zip' as media_type,
    doc_id || '.zip' as file_name,
    'https://api.edinet-fsa.go.jp/api/v2/documents/' || doc_id || '?type=1' as source_url,
    fetched_at
from {{ source('edinet_source', 'xbrl_files') }}
