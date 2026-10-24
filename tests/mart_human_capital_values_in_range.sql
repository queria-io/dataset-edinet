-- 入力誤りの補正が効いていることを検証する。結果が0行ならテスト成功。
--
-- 有報の「管理職に占める女性労働者の割合」「男女の賃金の差異」は 0〜1 の比率で
-- 開示する決まりだが、71.4 のように % の数字をそのまま入れた会社がある。
-- mart_human_capital では賃金の差異は 2 を、女性管理職比率・女性役員比率は 1 を
-- 超える値を % とみなして直しているので、直したあとにそれを超える値が残っていれば
-- 変換の取りこぼし。
-- 平均年間給与の桁誤り（5,000 万円以上）と負の臨時雇用人員は NULL にしている。
select
    doc_id,
    edinet_code,
    filer_name,
    period_end,
    female_manager_ratio,
    female_to_male_wage_ratio,
    female_to_male_wage_ratio_regular,
    female_to_male_wage_ratio_non_regular,
    female_officer_ratio,
    average_annual_salary,
    temporary_workers_consolidated,
    temporary_workers
from {{ ref('mart_human_capital') }}
where female_manager_ratio > 1
   or female_to_male_wage_ratio > 2
   or female_to_male_wage_ratio_regular > 2
   or female_to_male_wage_ratio_non_regular > 2
   or female_officer_ratio > 1
   or average_annual_salary >= 50000000
   or temporary_workers < 0
   or temporary_workers_consolidated < 0
