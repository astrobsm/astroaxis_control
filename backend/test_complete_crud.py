"""
Comprehensive CRUD Test Script for ASTRO-ASIX ERP
Tests data persistence and retrieval across all modules
"""
import asyncio
import httpx
from datetime import date, datetime
import json

BASE_URL = "http://127.0.0.1:8004"

# Test results tracking
results = {
    "passed": [],
    "failed": []
}

def log_test(module, action, success, message=""):
    """Log test results"""
    status = "✅ PASSED" if success else "❌ FAILED"
    test_name = f"{module} - {action}"
    print(f"{status}: {test_name} {message}")
    
    if success:
        results["passed"].append(test_name)
    else:
        results["failed"].append(test_name)

async def test_staff_crud():
    """Test Staff module CRUD operations"""
    print("\n" + "="*60)
    print("TESTING STAFF MODULE")
    print("="*60)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. CREATE Staff
        staff_data = {
            "first_name": "John",
            "last_name": "Doe",
            "phone": "08012345678",
            "position": "Production Manager",
            "payment_mode": "monthly",
            "monthly_salary": 150000.00,
            "bank_name": "First Bank",
            "bank_account_number": "1234567890",
            "bank_account_name": "John Doe",
            "hire_date": str(date.today()),
            "is_active": True
        }
        
        try:
            response = await client.post(f"{BASE_URL}/api/staff/staffs", json=staff_data)
            if response.status_code == 200:
                created_staff = response.json()
                staff_id = created_staff["id"]
                employee_id = created_staff.get("employee_id")
                clock_pin = created_staff.get("clock_pin")
                
                log_test("Staff", "CREATE", True, 
                        f"(ID: {staff_id[:8]}..., Employee ID: {employee_id}, PIN: {clock_pin})")
                
                # 2. RETRIEVE Staff by ID
                get_response = await client.get(f"{BASE_URL}/api/staff/staffs/{staff_id}")
                if get_response.status_code == 200:
                    retrieved_staff = get_response.json()
                    if retrieved_staff["first_name"] == "John":
                        log_test("Staff", "RETRIEVE BY ID", True)
                    else:
                        log_test("Staff", "RETRIEVE BY ID", False, "Data mismatch")
                else:
                    log_test("Staff", "RETRIEVE BY ID", False, f"Status: {get_response.status_code}")
                
                # 3. LIST All Staff
                list_response = await client.get(f"{BASE_URL}/api/staff/staffs")
                if list_response.status_code == 200:
                    staff_list = list_response.json()
                    if staff_list.get("total", 0) > 0:
                        found = any(s["id"] == staff_id for s in staff_list.get("items", []))
                        log_test("Staff", "LIST ALL", found)
                    else:
                        log_test("Staff", "LIST ALL", False, "Empty list")
                else:
                    log_test("Staff", "LIST ALL", False, f"Status: {list_response.status_code}")
                
                # 4. UPDATE Staff
                update_data = {"position": "Senior Production Manager"}
                update_response = await client.put(f"{BASE_URL}/api/staff/staffs/{staff_id}", json=update_data)
                if update_response.status_code == 200:
                    updated_staff = update_response.json()
                    if updated_staff["position"] == "Senior Production Manager":
                        log_test("Staff", "UPDATE", True)
                    else:
                        log_test("Staff", "UPDATE", False, "Update not applied")
                else:
                    log_test("Staff", "UPDATE", False, f"Status: {update_response.status_code}")
                
                # 5. DELETE Staff
                delete_response = await client.delete(f"{BASE_URL}/api/staff/staffs/{staff_id}")
                if delete_response.status_code in [200, 204]:
                    log_test("Staff", "DELETE", True)
                else:
                    log_test("Staff", "DELETE", False, f"Status: {delete_response.status_code}")
                
                return True
            else:
                log_test("Staff", "CREATE", False, f"Status: {response.status_code} - {response.text[:200]}")
                return False
                
        except Exception as e:
            log_test("Staff", "CREATE", False, f"Exception: {str(e)}")
            return False

async def test_customer_crud():
    """Test Customer module CRUD operations"""
    print("\n" + "="*60)
    print("TESTING CUSTOMER MODULE")
    print("="*60)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        customer_data = {
            "customer_code": f"CUST{datetime.now().microsecond}",
            "name": "Test Customer Ltd",
            "email": "customer@test.com",
            "phone": "08098765432",
            "address": "123 Test Street, Lagos",
            "credit_limit": 500000.00,
            "is_active": True
        }
        
        try:
            # CREATE
            response = await client.post(f"{BASE_URL}/api/sales/customers", json=customer_data)
            if response.status_code == 200:
                created = response.json()
                customer_id = created["id"]
                log_test("Customer", "CREATE", True, f"(ID: {customer_id[:8]}...)")
                
                # RETRIEVE
                get_response = await client.get(f"{BASE_URL}/api/sales/customers/{customer_id}")
                log_test("Customer", "RETRIEVE", get_response.status_code == 200)
                
                # LIST
                list_response = await client.get(f"{BASE_URL}/api/sales/customers")
                log_test("Customer", "LIST", list_response.status_code == 200)
                
                # UPDATE
                update_data = {"credit_limit": 750000.00}
                update_response = await client.put(f"{BASE_URL}/api/sales/customers/{customer_id}", json=update_data)
                log_test("Customer", "UPDATE", update_response.status_code == 200)
                
                # DELETE
                delete_response = await client.delete(f"{BASE_URL}/api/sales/customers/{customer_id}")
                log_test("Customer", "DELETE", delete_response.status_code in [200, 204])
                
                return True
            else:
                log_test("Customer", "CREATE", False, f"Status: {response.status_code}")
                return False
        except Exception as e:
            log_test("Customer", "CREATE", False, f"Exception: {str(e)}")
            return False

async def test_product_crud():
    """Test Product module CRUD operations"""
    print("\n" + "="*60)
    print("TESTING PRODUCT MODULE")
    print("="*60)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        product_data = {
            "sku": f"PRD{datetime.now().microsecond}",
            "name": "Test Wound Care Product",
            "description": "Advanced wound dressing",
            "manufacturer": "MedCorp",
            "unit": "box",
            "cost_price": 5000.00,
            "selling_price": 7500.00,
            "retail_price": 8000.00,
            "reorder_level": 50,
            "minimum_order_quantity": 10
        }
        
        try:
            # CREATE
            response = await client.post(f"{BASE_URL}/api/inventory/products", json=product_data)
            if response.status_code == 200:
                created = response.json()
                product_id = created["id"]
                log_test("Product", "CREATE", True, f"(ID: {product_id[:8]}...)")
                
                # RETRIEVE
                get_response = await client.get(f"{BASE_URL}/api/inventory/products/{product_id}")
                log_test("Product", "RETRIEVE", get_response.status_code == 200)
                
                # LIST
                list_response = await client.get(f"{BASE_URL}/api/inventory/products")
                log_test("Product", "LIST", list_response.status_code == 200)
                
                # UPDATE
                update_data = {"selling_price": 8500.00}
                update_response = await client.put(f"{BASE_URL}/api/inventory/products/{product_id}", json=update_data)
                log_test("Product", "UPDATE", update_response.status_code == 200)
                
                # DELETE
                delete_response = await client.delete(f"{BASE_URL}/api/inventory/products/{product_id}")
                log_test("Product", "DELETE", delete_response.status_code in [200, 204])
                
                return True
            else:
                log_test("Product", "CREATE", False, f"Status: {response.status_code}")
                return False
        except Exception as e:
            log_test("Product", "CREATE", False, f"Exception: {str(e)}")
            return False

async def test_warehouse_crud():
    """Test Warehouse module CRUD operations"""
    print("\n" + "="*60)
    print("TESTING WAREHOUSE MODULE")
    print("="*60)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        warehouse_data = {
            "code": f"WH{datetime.now().microsecond}",
            "name": "Main Warehouse",
            "location": "Lagos Industrial Area",
            "is_active": True
        }
        
        try:
            # CREATE
            response = await client.post(f"{BASE_URL}/api/inventory/warehouses", json=warehouse_data)
            if response.status_code == 200:
                created = response.json()
                warehouse_id = created["id"]
                log_test("Warehouse", "CREATE", True, f"(ID: {warehouse_id[:8]}...)")
                
                # RETRIEVE
                get_response = await client.get(f"{BASE_URL}/api/inventory/warehouses/{warehouse_id}")
                log_test("Warehouse", "RETRIEVE", get_response.status_code == 200)
                
                # LIST
                list_response = await client.get(f"{BASE_URL}/api/inventory/warehouses")
                log_test("Warehouse", "LIST", list_response.status_code == 200)
                
                # UPDATE
                update_data = {"location": "Ikeja Industrial Zone"}
                update_response = await client.put(f"{BASE_URL}/api/inventory/warehouses/{warehouse_id}", json=update_data)
                log_test("Warehouse", "UPDATE", update_response.status_code == 200)
                
                # DELETE
                delete_response = await client.delete(f"{BASE_URL}/api/inventory/warehouses/{warehouse_id}")
                log_test("Warehouse", "DELETE", delete_response.status_code in [200, 204])
                
                return True
            else:
                log_test("Warehouse", "CREATE", False, f"Status: {response.status_code}")
                return False
        except Exception as e:
            log_test("Warehouse", "CREATE", False, f"Exception: {str(e)}")
            return False

async def test_raw_material_crud():
    """Test Raw Material module CRUD operations"""
    print("\n" + "="*60)
    print("TESTING RAW MATERIAL MODULE")
    print("="*60)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        raw_material_data = {
            "sku": f"RM{datetime.now().microsecond}",
            "name": "Medical Grade Cotton",
            "manufacturer": "Cotton Corp",
            "unit": "kg",
            "unit_cost": 3500.00,
            "reorder_level": 100
        }
        
        try:
            # CREATE
            response = await client.post(f"{BASE_URL}/api/production/raw-materials", json=raw_material_data)
            if response.status_code == 200:
                created = response.json()
                rm_id = created["id"]
                log_test("Raw Material", "CREATE", True, f"(ID: {rm_id[:8]}...)")
                
                # RETRIEVE
                get_response = await client.get(f"{BASE_URL}/api/production/raw-materials/{rm_id}")
                log_test("Raw Material", "RETRIEVE", get_response.status_code == 200)
                
                # LIST
                list_response = await client.get(f"{BASE_URL}/api/production/raw-materials")
                log_test("Raw Material", "LIST", list_response.status_code == 200)
                
                # UPDATE
                update_data = {"unit_cost": 3800.00}
                update_response = await client.put(f"{BASE_URL}/api/production/raw-materials/{rm_id}", json=update_data)
                log_test("Raw Material", "UPDATE", update_response.status_code == 200)
                
                # DELETE
                delete_response = await client.delete(f"{BASE_URL}/api/production/raw-materials/{rm_id}")
                log_test("Raw Material", "DELETE", delete_response.status_code in [200, 204])
                
                return True
            else:
                log_test("Raw Material", "CREATE", False, f"Status: {response.status_code}")
                return False
        except Exception as e:
            log_test("Raw Material", "CREATE", False, f"Exception: {str(e)}")
            return False

async def test_attendance_crud():
    """Test Attendance module - Clock In/Out"""
    print("\n" + "="*60)
    print("TESTING ATTENDANCE MODULE")
    print("="*60)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # First create a staff member to test attendance
        staff_data = {
            "first_name": "Test",
            "last_name": "Attendance",
            "phone": "08011112222",
            "position": "Worker",
            "payment_mode": "hourly",
            "hourly_rate": 500.00,
            "is_active": True
        }
        
        try:
            # Create staff
            staff_response = await client.post(f"{BASE_URL}/api/staff/staffs", json=staff_data)
            if staff_response.status_code == 200:
                staff = staff_response.json()
                staff_id = staff["id"]
                clock_pin = staff["clock_pin"]
                
                # CLOCK IN
                clock_in_data = {"notes": "Morning shift"}
                clock_in_response = await client.post(
                    f"{BASE_URL}/api/attendance/clock-in",
                    json={"pin": clock_pin, "notes": "Morning shift"}
                )
                log_test("Attendance", "CLOCK IN", clock_in_response.status_code == 200)
                
                if clock_in_response.status_code == 200:
                    attendance = clock_in_response.json()
                    
                    # LIST Attendance
                    list_response = await client.get(f"{BASE_URL}/api/attendance/")
                    log_test("Attendance", "LIST", list_response.status_code == 200)
                    
                    # CLOCK OUT
                    clock_out_response = await client.post(f"{BASE_URL}/api/attendance/clock-out?staff_id={staff_id}")
                    log_test("Attendance", "CLOCK OUT", clock_out_response.status_code == 200)
                
                # Clean up: delete staff
                await client.delete(f"{BASE_URL}/api/staff/staffs/{staff_id}")
                return True
            else:
                log_test("Attendance", "SETUP (Create Staff)", False)
                return False
        except Exception as e:
            log_test("Attendance", "CLOCK IN/OUT", False, f"Exception: {str(e)}")
            return False

async def test_permissions_crud():
    """Test Permissions module CRUD operations"""
    print("\n" + "="*60)
    print("TESTING PERMISSIONS MODULE")
    print("="*60)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            # Initialize default permissions
            init_response = await client.post(f"{BASE_URL}/api/permissions/initialize")
            log_test("Permissions", "INITIALIZE", init_response.status_code == 200)
            
            # LIST Permissions
            list_response = await client.get(f"{BASE_URL}/api/permissions/")
            log_test("Permissions", "LIST", list_response.status_code == 200)
            
            if list_response.status_code == 200:
                permissions = list_response.json()
                if permissions.get("total", 0) > 0:
                    log_test("Permissions", "VERIFY COUNT", True, f"({permissions['total']} permissions)")
                else:
                    log_test("Permissions", "VERIFY COUNT", False, "No permissions found")
            
            return True
        except Exception as e:
            log_test("Permissions", "OPERATIONS", False, f"Exception: {str(e)}")
            return False

async def main():
    """Run all tests"""
    print("\n")
    print("╔" + "="*58 + "╗")
    print("║" + " "*15 + "ASTRO-ASIX ERP" + " "*15 + "        ║")
    print("║" + " "*10 + "COMPREHENSIVE CRUD TEST SUITE" + " "*9 + "     ║")
    print("╚" + "="*58 + "╝")
    print("\n🔍 Testing data persistence and retrieval across all modules...")
    print(f"📍 Target: {BASE_URL}\n")
    
    # Check if server is running
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            health_check = await client.get(f"{BASE_URL}/")
            if health_check.status_code != 200:
                print("❌ Server is not responding. Please start the backend server.")
                return
    except Exception as e:
        print(f"❌ Cannot connect to server at {BASE_URL}")
        print(f"   Error: {str(e)}")
        print("   Please ensure the backend server is running on port 8004")
        return
    
    print("✅ Server is running\n")
    
    # Run all tests
    await test_staff_crud()
    await test_customer_crud()
    await test_product_crud()
    await test_warehouse_crud()
    await test_raw_material_crud()
    await test_attendance_crud()
    await test_permissions_crud()
    
    # Print summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    print(f"✅ PASSED: {len(results['passed'])}")
    print(f"❌ FAILED: {len(results['failed'])}")
    
    if results['failed']:
        print("\n❌ Failed Tests:")
        for test in results['failed']:
            print(f"   - {test}")
    
    total = len(results['passed']) + len(results['failed'])
    if total > 0:
        success_rate = (len(results['passed']) / total) * 100
        print(f"\n📊 Success Rate: {success_rate:.1f}%")
        
        if success_rate == 100:
            print("\n🎉 ALL TESTS PASSED! Database persistence is working correctly.")
        elif success_rate >= 80:
            print("\n✅ Most tests passed. Minor issues detected.")
        else:
            print("\n⚠️  Multiple failures detected. Please review the errors above.")
    
    print("\n" + "="*60 + "\n")

if __name__ == "__main__":
    asyncio.run(main())
