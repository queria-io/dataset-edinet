{{ config(materialized='view') }}

select *
from {{ source('edinet_source', 'xbrl_contexts') }}
