-- 値の範囲を検証する。結果が0行ならテスト成功。
-- 電話番号の数字列は 0 で始まる 10〜11 桁だけを残しているので、それ以外が残っていれば
-- phone_digits の取りこぼし。連結子会社の数と研究開発費は負にならない。
select
    doc_id,
    filer_name,
    head_office_phone,
    head_office_phone_digits,
    contact_phone_digits,
    consolidated_subsidiaries,
    research_and_development_expenses
from {{ ref('mart_company_profiles') }}
where not regexp_full_match(head_office_phone_digits, '0[0-9]{9,10}')
   or not regexp_full_match(contact_phone_digits, '0[0-9]{9,10}')
   or consolidated_subsidiaries < 0
   or research_and_development_expenses < 0
