#!/usr/bin/env bash
set -euo pipefail
# documents は日付単位の増分取得で、公開済みカタログの取り込みが前提になる
# (未取得日だけを取る)。queria sync は pull から始まるので、ここで pull しない
exec "$(dirname "$0")/../shared/scripts/build-dataset.sh"
