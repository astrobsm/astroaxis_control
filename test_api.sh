#!/bin/bash
BASE=https://erp.bonnesantemedicals.com/api
ENDPOINTS=(
health
staff/staffs/
attendance/
products/
products/categories
raw-materials/
stock/
warehouses/
production/orders
sales/orders
sales/customers
stock-management/movements
bom/
settings/
financial/summary
notifications/
production-consumables/
machines-equipment/
production-completions/
marketing/plans
marketing/activities
hr-customercare/tickets
payment-tracking/
procurement/expenses
logistics/manifests
warehouse-transfers/
returns/
damaged-transfers/
receive-transfers/
legacy-debts/
)
echo "=== API ENDPOINT TESTING ==="
PASS=0
FAIL=0
for ep in ${ENDPOINTS[@]}; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' $BASE/$ep)
  if [ "$CODE" = "200" ]; then
    echo "OK  $CODE | $ep"
    PASS=$((PASS+1))
  else
    echo "ERR $CODE | $ep"
    FAIL=$((FAIL+1))
  fi
done
echo "=== RESULTS: $PASS passed, $FAIL failed ==="
