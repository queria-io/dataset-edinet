---
$schema: https://queria.io/schemas/table/v1.json
table: main.raw_xbrl_contexts
description: XBRL インスタンスの context。期間と、軸・メンバーの組
fields:
  - name: doc_id
    description: 書類管理番号
  - name: context_id
    description: コンテキスト ID
  - name: period_start
    description: 期間の開始日
  - name: period_end
    description: 期間の終了日
  - name: instant
    description: 時点の日付
  - name: members
    description: 軸とメンバーの組の JSON（[[軸, メンバー], …]）
published: false
---
