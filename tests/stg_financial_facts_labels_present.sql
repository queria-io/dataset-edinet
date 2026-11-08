-- 直近 1 年の XBRL の書類で、項目名の付かない要素が無いこと。
-- タクソノミの新しい年版を取り損ねたとき、会社のラベルを読み損ねたときに落ちる
select element_id, count(*) as n
from {{ ref('stg_financial_facts') }}
where source = 'xbrl'
    and item_name is null
    and submit_date >= current_date - interval 365 day
group by element_id
