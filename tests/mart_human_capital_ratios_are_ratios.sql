-- 割合の列に % のまま入力された値が残っていないことを検証する。結果が0行ならテスト成功。
--
-- 有報の「管理職に占める女性労働者の割合」「男女の賃金の差異」は 0〜1 の比率で
-- 開示する決まりだが、71.4 のように % の数字をそのまま入れた会社がある。
-- mart_human_capital では 2 を超える値を % とみなして直しているので、
-- 直したあとに 2 を超える値が残っていれば変換の取りこぼし。
select
    doc_id,
    edinet_code,
    filer_name,
    period_end,
    female_manager_ratio,
    female_to_male_wage_ratio,
    female_to_male_wage_ratio_regular,
    female_to_male_wage_ratio_non_regular,
    female_officer_ratio
from {{ ref('mart_human_capital') }}
where female_manager_ratio > 2
   or female_to_male_wage_ratio > 2
   or female_to_male_wage_ratio_regular > 2
   or female_to_male_wage_ratio_non_regular > 2
   or female_officer_ratio > 1
