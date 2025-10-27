"""
Comprehensive Module Testing for ASTROAXIS ERP
Tests all modules systematically: Authentication, Staff, Warehouse, Customer, etc.
"""
import requests
import json
from typing import Dict, Any

BASE_URL = "http://127.0.0.1:8004"
token = None
test_results = []

def log_test(module: str, test_name: str, passed: bool, details: str = ""):
    """Log test result"""
    status = "✅ PASSED" if passed else "❌ FAILED"
    result = f"{status} | {module} | {test_name}"
    if details:
        result += f" | {details}"
    test_results.append(result)
    print(result)

# ==================== MODULE 1: AUTHENTICATION ====================
print("\n" + "="*70)
print("MODULE 1: AUTHENTICATION TESTING")
print("="*70)

# Test 1.1: Email Login
try:
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "admin@astroasix.com", "password": "Admin123!"}
    )
    if response.status_code == 200:
        data = response.json()
        token = data.get("access_token")
        user = data.get("user", {})
        log_test("AUTH", "Email Login", True, 
                f"User: {user.get('first_name')} {user.get('last_name')}, Role: {user.get('role')}")
    else:
        log_test("AUTH", "Email Login", False, f"Status: {response.status_code}")
except Exception as e:
    log_test("AUTH", "Email Login", False, str(e))

# Test 1.2: Phone Login
try:
    response = requests.post(
        f"{BASE_URL}/api/auth/login-phone",
        json={"phone": "08033328385", "password": "Admin123!", "role": "admin"}
    )
    if response.status_code == 200:
        data = response.json()
        log_test("AUTH", "Phone Login", True, f"Token received: {len(data.get('access_token', ''))} chars")
    else:
        log_test("AUTH", "Phone Login", False, f"Status: {response.status_code}, Response: {response.text[:100]}")
except Exception as e:
    log_test("AUTH", "Phone Login", False, str(e))

# Test 1.3: Invalid Credentials
try:
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": "wrong@test.com", "password": "wrong"}
    )
    if response.status_code == 401 or response.status_code == 400:
        log_test("AUTH", "Invalid Credentials Rejection", True, "Correctly rejected")
    else:
        log_test("AUTH", "Invalid Credentials Rejection", False, f"Unexpected status: {response.status_code}")
except Exception as e:
    log_test("AUTH", "Invalid Credentials Rejection", False, str(e))

# ==================== MODULE 2: STAFF (WITH NEW FEATURES) ====================
print("\n" + "="*70)
print("MODULE 2: STAFF TESTING (Dynamic Payment Fields)")
print("="*70)

headers = {"Authorization": f"Bearer {token}"}

# Test 2.1: Get All Staff
try:
    response = requests.get(f"{BASE_URL}/api/staff/staffs/", headers=headers)
    if response.status_code == 200:
        staff_data = response.json()
        staff_list = staff_data.get('items', [])  # Handle paginated response
        log_test("STAFF", "Get All Staff", True, f"Found {len(staff_list)} staff members")
    else:
        log_test("STAFF", "Get All Staff", False, f"Status: {response.status_code}")
except Exception as e:
    log_test("STAFF", "Get All Staff", False, str(e))

# Test 2.2: Create Staff with Monthly Payment
try:
    new_staff = {
        "first_name": "Test",
        "last_name": "Monthly",
        "phone": "08012345601",
        "position": "Test Position",
        "payment_mode": "monthly",
        "monthly_salary": 150000,
        "date_of_birth": "1990-01-01"
    }
    response = requests.post(f"{BASE_URL}/api/staff/staffs", json=new_staff, headers=headers)
    if response.status_code == 200 or response.status_code == 201:
        created = response.json()
        salary = created.get('monthly_salary', 0)
        salary_display = f"₦{float(salary):,.0f}" if salary else "₦0"
        log_test("STAFF", "Create Staff (Monthly)", True, 
                f"ID: {created.get('employee_id')}, Salary: {salary_display}")
    else:
        log_test("STAFF", "Create Staff (Monthly)", False, 
                f"Status: {response.status_code}, Error: {response.text[:100]}")
except Exception as e:
    log_test("STAFF", "Create Staff (Monthly)", False, str(e))

# Test 2.3: Create Staff with Hourly Payment
try:
    new_staff = {
        "first_name": "Test",
        "last_name": "Hourly",
        "phone": "08012345602",
        "position": "Test Position",
        "payment_mode": "hourly",
        "hourly_rate": 2500,
        "date_of_birth": "1990-01-01"
    }
    response = requests.post(f"{BASE_URL}/api/staff/staffs", json=new_staff, headers=headers)
    if response.status_code == 200 or response.status_code == 201:
        created = response.json()
        rate = created.get('hourly_rate', 0)
        rate_display = f"₦{float(rate):,.0f}/hr" if rate else "₦0/hr"
        log_test("STAFF", "Create Staff (Hourly)", True, 
                f"ID: {created.get('employee_id')}, Rate: {rate_display}")
    else:
        log_test("STAFF", "Create Staff (Hourly)", False, 
                f"Status: {response.status_code}, Error: {response.text[:100]}")
except Exception as e:
    log_test("STAFF", "Create Staff (Hourly)", False, str(e))

# Test 2.4: Birthday Notification
try:
    response = requests.get(f"{BASE_URL}/api/staff/birthdays/upcoming?days_ahead=30", headers=headers)
    if response.status_code == 200:
        birthdays = response.json()
        log_test("STAFF", "Birthday Notifications", True, f"{len(birthdays)} upcoming birthdays")
    else:
        log_test("STAFF", "Birthday Notifications", False, f"Status: {response.status_code}")
except Exception as e:
    log_test("STAFF", "Birthday Notifications", False, str(e))

# ==================== MODULE 3: WAREHOUSES (WITH MANAGER FEATURE) ====================
print("\n" + "="*70)
print("MODULE 3: WAREHOUSE TESTING (Manager Assignment)")
print("="*70)

# Test 3.1: Get All Warehouses
try:
    response = requests.get(f"{BASE_URL}/api/warehouses/", headers=headers)
    if response.status_code == 200:
        warehouses = response.json()
        log_test("WAREHOUSE", "Get All Warehouses", True, f"Found {len(warehouses)} warehouses")
    else:
        log_test("WAREHOUSE", "Get All Warehouses", False, f"Status: {response.status_code}")
except Exception as e:
    log_test("WAREHOUSE", "Get All Warehouses", False, str(e))

# Test 3.2: Create Warehouse Without Manager
try:
    new_warehouse = {
        "code": f"WH-TEST-{int(__import__('time').time())}",
        "name": f"Test Warehouse No Mgr {__import__('time').time()}",
        "location": "Test Location"
    }
    response = requests.post(f"{BASE_URL}/api/warehouses/", json=new_warehouse, headers=headers)
    if response.status_code == 200 or response.status_code == 201:
        created = response.json()
        log_test("WAREHOUSE", "Create Without Manager", True, 
                f"ID: {created.get('id')}, Manager: {created.get('manager_id', 'None')}")
    else:
        log_test("WAREHOUSE", "Create Without Manager", False, f"Status: {response.status_code}")
except Exception as e:
    log_test("WAREHOUSE", "Create Without Manager", False, str(e))

# Test 3.3: Create Warehouse With Manager (if staff exist)
try:
    # Get first staff member
    staff_response = requests.get(f"{BASE_URL}/api/staff/staffs/", headers=headers)
    if staff_response.status_code == 200:
        staff_data = staff_response.json()
        staff_list = staff_data.get('items', [])  # Handle paginated response
        if len(staff_list) > 0:
            first_staff = staff_list[0]
            new_warehouse = {
                "code": f"WH-MGR-{int(__import__('time').time())}",
                "name": f"Test Warehouse With Mgr {__import__('time').time()}",
                "location": "Test Location 2",
                "manager_id": first_staff.get("id")
            }
            response = requests.post(f"{BASE_URL}/api/warehouses/", json=new_warehouse, headers=headers)
            if response.status_code == 200 or response.status_code == 201:
                created = response.json()
                log_test("WAREHOUSE", "Create With Manager", True, 
                        f"Manager: {first_staff.get('first_name')} {first_staff.get('last_name')}")
            else:
                log_test("WAREHOUSE", "Create With Manager", False, f"Status: {response.status_code}")
        else:
            log_test("WAREHOUSE", "Create With Manager", False, "No staff available")
    else:
        log_test("WAREHOUSE", "Create With Manager", False, f"Staff fetch failed: {staff_response.status_code}")
except Exception as e:
    log_test("WAREHOUSE", "Create With Manager", False, str(e))

# ==================== MODULE 4: CUSTOMERS ====================
print("\n" + "="*70)
print("MODULE 4: CUSTOMER TESTING")
print("="*70)

# Test 4.1: Get All Customers
try:
    response = requests.get(f"{BASE_URL}/api/sales/customers", headers=headers)
    if response.status_code == 200:
        customers = response.json()
        log_test("CUSTOMER", "Get All Customers", True, f"Found {len(customers)} customers")
    else:
        log_test("CUSTOMER", "Get All Customers", False, f"Status: {response.status_code}")
except Exception as e:
    log_test("CUSTOMER", "Get All Customers", False, str(e))

# Test 4.2: Create Customer
try:
    new_customer = {
        "customer_code": f"CUST{int(__import__('time').time())}",
        "name": "Test Customer",
        "email": f"customer.{__import__('time').time()}@test.com",
        "phone": "08012345678",
        "address": "Test Address",
        "credit_limit": 500000
    }
    response = requests.post(f"{BASE_URL}/api/sales/customers", json=new_customer, headers=headers)
    if response.status_code == 200 or response.status_code == 201:
        created = response.json()
        credit_limit = created.get('credit_limit', 0)
        credit_display = f"₦{float(credit_limit):,.0f}" if credit_limit else "₦0"
        log_test("CUSTOMER", "Create Customer", True, 
                f"Code: {created.get('customer_code')}, Credit: {credit_display}")
    else:
        log_test("CUSTOMER", "Create Customer", False, 
                f"Status: {response.status_code}, Error: {response.text[:100]}")
except Exception as e:
    log_test("CUSTOMER", "Create Customer", False, str(e))

# ==================== MODULE 5: PRODUCTS ====================
print("\n" + "="*70)
print("MODULE 5: PRODUCTS TESTING (Pricing Fields)")
print("="*70)

# Test 5.1: Get All Products
try:
    response = requests.get(f"{BASE_URL}/api/products/", headers=headers)
    if response.status_code == 200:
        products = response.json()
        log_test("PRODUCT", "Get All Products", True, f"Found {len(products)} products")
    else:
        log_test("PRODUCT", "Get All Products", False, f"Status: {response.status_code}")
except Exception as e:
    log_test("PRODUCT", "Get All Products", False, str(e))

# Test 5.2: Create Product with Pricing
try:
    new_product = {
        "sku": f"TEST-SKU-{int(__import__('time').time())}",
        "name": "Test Product",
        "description": "Test product with pricing",
        "cost_price": 5000,
        "selling_price": 8000,
        "retail_price": 9000,
        "wholesale_price": 7000,
        "stock_quantity": 100,
        "manufacturer": "Test Manufacturer",
        "reorder_level": 10
    }
    response = requests.post(f"{BASE_URL}/api/products/", json=new_product, headers=headers)
    if response.status_code == 200 or response.status_code == 201:
        created = response.json()
        log_test("PRODUCT", "Create with Pricing", True, 
                f"SKU: {created.get('sku')}, Retail: ₦{created.get('retail_price', 0):,.0f}")
    else:
        log_test("PRODUCT", "Create with Pricing", False, f"Status: {response.status_code}")
except Exception as e:
    log_test("PRODUCT", "Create with Pricing", False, str(e))

# ==================== MODULE 6: STOCK MANAGEMENT ====================
print("\n" + "="*70)
print("MODULE 6: STOCK MANAGEMENT TESTING")
print("="*70)

# Test 6.1: Get Product Levels
try:
    response = requests.get(f"{BASE_URL}/api/stock-management/product-levels", headers=headers)
    if response.status_code == 200:
        levels = response.json()
        log_test("STOCK", "Get Product Levels", True, f"Found {len(levels)} product stock levels")
    else:
        log_test("STOCK", "Get Product Levels", False, f"Status: {response.status_code}")
except Exception as e:
    log_test("STOCK", "Get Product Levels", False, str(e))

# Test 6.2: Get Raw Material Levels
try:
    response = requests.get(f"{BASE_URL}/api/stock-management/raw-material-levels", headers=headers)
    if response.status_code == 200:
        levels = response.json()
        log_test("STOCK", "Get Raw Material Levels", True, f"Found {len(levels)} raw material levels")
    else:
        log_test("STOCK", "Get Raw Material Levels", False, f"Status: {response.status_code}")
except Exception as e:
    log_test("STOCK", "Get Raw Material Levels", False, str(e))

# ==================== MODULE 7: SALES ====================
print("\n" + "="*70)
print("MODULE 7: SALES TESTING")
print("="*70)

# Test 7.1: Get All Sales Orders
try:
    response = requests.get(f"{BASE_URL}/api/sales/orders", headers=headers)
    if response.status_code == 200:
        orders = response.json()
        log_test("SALES", "Get All Orders", True, f"Found {len(orders)} sales orders")
    else:
        log_test("SALES", "Get All Orders", False, f"Status: {response.status_code}")
except Exception as e:
    log_test("SALES", "Get All Orders", False, str(e))

# ==================== MODULE 8: PRODUCTION ====================
print("\n" + "="*70)
print("MODULE 8: PRODUCTION TESTING")
print("="*70)

# Test 8.1: Get All Production Orders
try:
    response = requests.get(f"{BASE_URL}/api/production/orders", headers=headers)
    if response.status_code == 200:
        orders = response.json()
        log_test("PRODUCTION", "Get All Orders", True, f"Found {len(orders)} production orders")
    else:
        log_test("PRODUCTION", "Get All Orders", False, f"Status: {response.status_code}")
except Exception as e:
    log_test("PRODUCTION", "Get All Orders", False, str(e))

# ==================== MODULE 9: ATTENDANCE ====================
print("\n" + "="*70)
print("MODULE 9: ATTENDANCE TESTING")
print("="*70)

# Test 9.1: Get All Attendance Records
try:
    response = requests.get(f"{BASE_URL}/api/attendance/", headers=headers)
    if response.status_code == 200:
        records = response.json()
        log_test("ATTENDANCE", "Get All Records", True, f"Found {len(records)} attendance records")
    else:
        log_test("ATTENDANCE", "Get All Records", False, f"Status: {response.status_code}")
except Exception as e:
    log_test("ATTENDANCE", "Get All Records", False, str(e))

# ==================== MODULE 10: RAW MATERIALS ====================
print("\n" + "="*70)
print("MODULE 10: RAW MATERIALS TESTING")
print("="*70)

# Test 10.1: Get All Raw Materials
try:
    response = requests.get(f"{BASE_URL}/api/raw-materials/", headers=headers)
    if response.status_code == 200:
        materials = response.json()
        log_test("RAW_MATERIALS", "Get All Materials", True, f"Found {len(materials)} raw materials")
    else:
        log_test("RAW_MATERIALS", "Get All Materials", False, f"Status: {response.status_code}")
except Exception as e:
    log_test("RAW_MATERIALS", "Get All Materials", False, str(e))

# ==================== SUMMARY ====================
print("\n" + "="*70)
print("TEST SUMMARY")
print("="*70)
passed = sum(1 for r in test_results if "✅ PASSED" in r)
failed = sum(1 for r in test_results if "❌ FAILED" in r)
total = len(test_results)

print(f"\n📊 Total Tests: {total}")
print(f"✅ Passed: {passed} ({passed/total*100:.1f}%)")
print(f"❌ Failed: {failed} ({failed/total*100:.1f}%)")

if failed > 0:
    print("\n❌ FAILED TESTS:")
    for result in test_results:
        if "❌ FAILED" in result:
            print(f"  {result}")

print("\n" + "="*70)
