#!/usr/bin/env bash
set -euo pipefail
# documents は日付単位の増分取得で、公開済みカタログの取り込みが前提になる
# (未取得日だけを取る)。build-fdl.sh は公開中のバージョンの fdl checkout から始まる
# 記述 (テーブルの Markdown) の無いテーブル (取り込んだままの _source と dbt の中間テーブル) は掲載しない
export FDL_UNDESCRIBED="${FDL_UNDESCRIBED:-unlisted}"
# XBRL の ZIP (EDINET_XBRL_DIR) を fdl のアセットとして push する。
# 渡されないとき (GitHub Actions) はリポジトリの .xbrl に置く。置かないと ZIP がアセットに残らない
export EDINET_XBRL_DIR="${EDINET_XBRL_DIR:-$PWD/.xbrl}"
export FDL_ASSETS="${FDL_ASSETS:-$EDINET_XBRL_DIR}"
exec "$(dirname "$0")/../shared/scripts/build-fdl.sh" "$@"
