#!/bin/bash
BASE=https://erp.bonnesantemedicals.com/api
echo "=== COMPREHENSIVE API ENDPOINT TESTING ==="
echo ""
PASS=0
FAIL=0
test_ep() {
  local ep=$1
  local label=$2
  CODE=$(curl -s -o /dev/null -w '%{http_code}' $BASE/$ep)
  if [ "$CODE" = "200" ]; then
    echo "OK  $CODE | $label ($ep)"
    PASS=$((PASS+1))
  else
    echo "ERR $CODE | $label ($ep)"
    FAIL=$((FAIL+1))
  fi
}

echo "--- CORE ---"
test_ep "health" "Health Check"

echo "--- STAFF ---"
test_ep "staff/staffs" "Staff List"
test_ep "staff/employees" "Employee List"
test_ep "staff/departments" "Departments"
test_ep "staff/birthdays/upcoming" "Upcoming Birthdays"
test_ep "staff/dashboard/stats" "Staff Dashboard Stats"
test_ep "staff/payroll/dashboard" "Payroll Dashboard"
test_ep "staff/payroll/entries" "Payroll Entries"
test_ep "staff/work-logs" "Work Logs"

echo "--- ATTENDANCE ---"
test_ep "attendance/" "Attendance List"
test_ep "attendance/best-performers" "Best Performers"
test_ep "attendance/detailed-log" "Detailed Log"
test_ep "attendance/status" "Attendance Status"

echo "--- PRODUCTS ---"
test_ep "products/" "Products List"

echo "--- RAW MATERIALS ---"
test_ep "raw-materials/" "Raw Materials List"
test_ep "raw-materials/next-sku" "Next SKU"

echo "--- STOCK ---"
test_ep "stock/levels" "Stock Levels"
test_ep "stock/movements" "Stock Movements"
test_ep "stock/valuation" "Stock Valuation"
test_ep "stock/intake/" "Stock Intake"

echo "--- WAREHOUSES ---"
test_ep "warehouses/" "Warehouses"

echo "--- PRODUCTION ---"
test_ep "production/orders" "Production Orders"
test_ep "production/dashboard/stats" "Production Dashboard"

echo "--- SALES ---"
test_ep "sales/orders" "Sales Orders"
test_ep "sales/customers" "Customers"

echo "--- STOCK MANAGEMENT ---"
test_ep "stock-management/product-levels" "Product Stock Levels"
test_ep "stock-management/raw-material-levels" "Raw Material Stock Levels"
test_ep "stock-management/analysis" "Stock Analysis"

echo "--- BOM ---"
test_ep "bom/products-with-bom" "Products with BOM"

echo "--- SETTINGS ---"
test_ep "settings/" "Settings"
test_ep "settings/custom-fields" "Custom Fields"

echo "--- FINANCIAL ---"
test_ep "financial/company-status" "Company Financial Status"

echo "--- NOTIFICATIONS ---"
test_ep "notifications/status" "Notification Status"

echo "--- PRODUCTION CONSUMABLES ---"
test_ep "production-consumables/" "Consumables List"
test_ep "production-consumables/low-stock" "Low Stock Consumables"

echo "--- MACHINES ---"
test_ep "machines/" "Machines List"
test_ep "machines/dashboard/summary" "Machines Dashboard"

echo "--- PRODUCTION COMPLETIONS ---"
test_ep "production-completions/" "Completions List"
test_ep "production-completions/daily-staff-summary" "Daily Staff Summary"

echo "--- MARKETING ---"
test_ep "marketing/plans" "Marketing Plans"
test_ep "marketing/dashboard" "Marketing Dashboard"
test_ep "marketing/logs" "Marketing Logs"
test_ep "marketing/proposals" "Marketing Proposals"
test_ep "marketing/products-catalog" "Products Catalog"

echo "--- HR & CUSTOMER CARE ---"
test_ep "hr-customercare/dashboard" "HR Dashboard"
test_ep "hr-customercare/customers" "Customers"
test_ep "hr-customercare/staff" "Staff"
test_ep "hr-customercare/sales-orders" "Sales Orders"
test_ep "hr-customercare/attendance-log" "Attendance Log"
test_ep "hr-customercare/staff-performance" "Staff Performance"

echo "--- PAYMENT TRACKING ---"
test_ep "payment-tracking/debtors" "Debtors"
test_ep "payment-tracking/invoices" "Invoices"
test_ep "payment-tracking/reconciliation" "Reconciliation"
test_ep "payment-tracking/reminders" "Reminders"

echo "--- PROCUREMENT ---"
test_ep "procurement/expenses" "Expenses"
test_ep "procurement/orders" "Purchase Orders"
test_ep "procurement/requests" "Procurement Requests"
test_ep "procurement/invoices" "Procurement Invoices"
test_ep "procurement/dashboard" "Procurement Dashboard"

echo "--- LOGISTICS ---"
test_ep "logistics/manifests" "Manifests"
test_ep "logistics/dashboard" "Logistics Dashboard"
test_ep "logistics/analytics" "Logistics Analytics"

echo "--- TRANSFERS ---"
test_ep "transfers/" "Warehouse Transfers"
test_ep "transfers/summary" "Transfer Summary"

echo "--- RETURNS ---"
test_ep "returns/" "Returns"
test_ep "returns/summary" "Returns Summary"

echo "--- DAMAGED TRANSFERS ---"
test_ep "damaged-transfers/" "Damaged Transfers"
test_ep "damaged-transfers/summary" "Damaged Summary"

echo "--- RECEIVE TRANSFERS ---"
test_ep "receive-transfers/" "Receive Transfers"
test_ep "receive-transfers/summary" "Receive Summary"

echo "--- LEGACY DEBTS ---"
test_ep "legacy-debts/" "Legacy Debts"
test_ep "legacy-debts/summary/stats" "Debt Stats"

echo "--- AUTH ---"
test_ep "auth/users" "User List"
test_ep "auth/modules/list" "Module List"

echo "--- PERMISSIONS ---"
test_ep "permissions/" "Permissions List"
test_ep "permissions/modules" "Permission Modules"

echo ""
echo "=== RESULTS: $PASS passed, $FAIL failed ==="
