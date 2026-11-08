{{ config(materialized='view') }}

select *
from {{ source('edinet_source', 'affiliated_entities') }}
