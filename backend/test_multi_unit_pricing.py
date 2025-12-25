#!/usr/bin/env python3
"""
Direct HTTP test for multi-unit pricing functionality.
Tests against a running server (http://localhost:8004)
No pytest or complex fixtures required - just plain requests.
"""
import requests
import json
import sys
from datetime import datetime

BASE_URL = "http://localhost:8004"

def test_multi_unit_pricing():
    """
    Test complete multi-unit pricing flow:
    1. Create product with multiple unit pricing (each, box, carton)
    2. Create customer
    3. Create sales order with specific unit selection
    4. Retrieve order and verify unit & price persistence
    """
    print("\n" + "="*70)
    print("🧪 MULTI-UNIT PRICING TEST SUITE")
    print("="*70)
    
    # Step 1: Create product with multi-unit pricing
    print("\n📦 STEP 1: Creating product with multi-unit pricing...")
    print("-" * 70)
    
    product_data = {
        "sku": f"TEST-WIDGET-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "name": "Test Widget - Multi-Unit",
        "description": "Widget for testing multi-unit pricing",
        "manufacturer": "Test Manufacturing Ltd",
        "unit": "each",
        "reorder_level": 50,
        "pricing": [
            {
                "unit": "each",
                "cost_price": 10.00,
                "retail_price": 15.00,
                "wholesale_price": 12.50
            },
            {
                "unit": "box",
                "cost_price": 90.00,
                "retail_price": 135.00,
                "wholesale_price": 112.50
            },
            {
                "unit": "carton",
                "cost_price": 450.00,
                "retail_price": 675.00,
                "wholesale_price": 562.50
            }
        ]
    }
    
    try:
        response = requests.post(f"{BASE_URL}/api/products/", json=product_data, timeout=10)
        
        # Show detailed error info
        if response.status_code != 200:
            print(f"❌ FAILED: HTTP {response.status_code}")
            print(f"   Response headers: {dict(response.headers)}")
            print(f"   Response body: {response.text}")
            return False
        
        response.raise_for_status()
        
        product_response = response.json()
        if not product_response.get("success"):
            print(f"❌ FAILED: Product creation not successful")
            print(f"   Response: {json.dumps(product_response, indent=2)}")
            return False
        
        product_id = product_response["data"]["id"]
        pricing = product_response["data"]["pricing"]
        
        print(f"✅ Product created successfully!")
        print(f"   Product ID: {product_id}")
        print(f"   SKU: {product_data['sku']}")
        print(f"   Pricing entries: {len(pricing)}")
        
        if len(pricing) != 3:
            print(f"❌ FAILED: Expected 3 pricing entries, got {len(pricing)}")
            return False
        
        units = {p["unit"] for p in pricing}
        print(f"   Units available: {', '.join(sorted(units))}")
        
        if units != {"each", "box", "carton"}:
            print(f"❌ FAILED: Expected units {{each, box, carton}}, got {units}")
            return False
        
    except requests.exceptions.RequestException as e:
        print(f"❌ FAILED: Request error - {e}")
        return False
    except Exception as e:
        print(f"❌ FAILED: {e}")
        return False
    
    # Step 2: Create customer
    print("\n👤 STEP 2: Creating test customer...")
    print("-" * 70)
    
    customer_data = {
        "customer_code": f"CUST-TEST-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "name": "Multi-Unit Test Customer",
        "email": "multiunit@test.com",
        "phone": "+234-800-TEST-001",
        "credit_limit": 100000.00
    }
    
    try:
        response = requests.post(f"{BASE_URL}/api/sales/customers/", json=customer_data, timeout=10)
        response.raise_for_status()
        
        customer_response = response.json()
        customer_id = customer_response["data"]["id"]
        
        print(f"✅ Customer created successfully!")
        print(f"   Customer ID: {customer_id}")
        print(f"   Code: {customer_data['customer_code']}")
        
    except Exception as e:
        print(f"❌ FAILED: {e}")
        return False
    
    # Step 3: Create sales order with 'box' unit
    print("\n🛒 STEP 3: Creating sales order with 'box' unit...")
    print("-" * 70)
    
    sales_order_data = {
        "customer_id": customer_id,
        "order_date": "2025-11-11",
        "status": "pending",
        "notes": "Test order for multi-unit pricing - BOX unit",
        "lines": [
            {
                "product_id": product_id,
                "unit": "box",  # ⭐ Explicitly selecting 'box' unit
                "quantity": 5,
                "unit_price": 135.00,  # Retail price for box
                "discount": 0
            }
        ]
    }
    
    try:
        response = requests.post(f"{BASE_URL}/api/sales/orders/", json=sales_order_data, timeout=10)
        response.raise_for_status()
        
        order_response = response.json()
        order_id = order_response["data"]["id"]
        
        print(f"✅ Sales order created successfully!")
        print(f"   Order ID: {order_id}")
        print(f"   Unit specified: box")
        print(f"   Quantity: 5")
        print(f"   Unit price: ₦135.00")
        
    except Exception as e:
        print(f"❌ FAILED: {e}")
        if hasattr(e, 'response') and e.response:
            print(f"   Response: {e.response.text}")
        return False
    
    # Step 4: Retrieve and verify unit persistence
    print("\n🔍 STEP 4: Retrieving order to verify unit & price persistence...")
    print("-" * 70)
    
    try:
        response = requests.get(f"{BASE_URL}/api/sales/orders/{order_id}", timeout=10)
        response.raise_for_status()
        
        retrieved_order = response.json()["data"]
        
        if len(retrieved_order["lines"]) != 1:
            print(f"❌ FAILED: Expected 1 order line, got {len(retrieved_order['lines'])}")
            return False
        
        order_line = retrieved_order["lines"][0]
        
        # Critical verification - unit must be 'box'
        if order_line.get("unit") != "box":
            print(f"❌ UNIT PERSISTENCE FAILED!")
            print(f"   Expected unit: 'box'")
            print(f"   Actual unit: '{order_line.get('unit')}'")
            print(f"   Full line data: {json.dumps(order_line, indent=2)}")
            return False
        
        # Verify price
        if float(order_line["unit_price"]) != 135.00:
            print(f"❌ PRICE PERSISTENCE FAILED!")
            print(f"   Expected price: ₦135.00")
            print(f"   Actual price: ₦{order_line['unit_price']}")
            return False
        
        # Verify quantity
        if float(order_line["quantity"]) != 5:
            print(f"❌ QUANTITY MISMATCH!")
            print(f"   Expected: 5")
            print(f"   Actual: {order_line['quantity']}")
            return False
        
        # Calculate expected total
        expected_total = 5 * 135.00
        if float(order_line["line_total"]) != expected_total:
            print(f"❌ TOTAL CALCULATION FAILED!")
            print(f"   Expected: ₦{expected_total}")
            print(f"   Actual: ₦{order_line['line_total']}")
            return False
        
        print(f"✅ ALL VERIFICATIONS PASSED!")
        print(f"   ✓ Unit persistence: {order_line['unit']}")
        print(f"   ✓ Price persistence: ₦{order_line['unit_price']}")
        print(f"   ✓ Quantity: {order_line['quantity']}")
        print(f"   ✓ Line total: ₦{order_line['line_total']}")
        
    except Exception as e:
        print(f"❌ FAILED: {e}")
        return False
    
    # Step 5: Test with 'carton' unit
    print("\n📦 STEP 5: Testing with 'carton' unit...")
    print("-" * 70)
    
    sales_order_data_2 = {
        "customer_id": customer_id,
        "order_date": "2025-11-11",
        "status": "pending",
        "notes": "Test order for multi-unit pricing - CARTON unit",
        "lines": [
            {
                "product_id": product_id,
                "unit": "carton",  # ⭐ Testing with carton
                "quantity": 2,
                "unit_price": 675.00,
                "discount": 0
            }
        ]
    }
    
    try:
        response = requests.post(f"{BASE_URL}/api/sales/orders/", json=sales_order_data_2, timeout=10)
        response.raise_for_status()
        order_2_id = response.json()["data"]["id"]
        
        # Retrieve and verify
        response = requests.get(f"{BASE_URL}/api/sales/orders/{order_2_id}", timeout=10)
        response.raise_for_status()
        order_2_line = response.json()["data"]["lines"][0]
        
        if order_2_line["unit"] != "carton":
            print(f"❌ Carton unit persistence failed: got '{order_2_line['unit']}'")
            return False
        
        if float(order_2_line["unit_price"]) != 675.00:
            print(f"❌ Carton price persistence failed: got ₦{order_2_line['unit_price']}")
            return False
        
        print(f"✅ Carton unit test passed!")
        print(f"   ✓ Unit: {order_2_line['unit']}")
        print(f"   ✓ Price: ₦{order_2_line['unit_price']}")
        print(f"   ✓ Total: ₦{order_2_line['line_total']}")
        
    except Exception as e:
        print(f"❌ FAILED: {e}")
        return False
    
    # Final summary
    print("\n" + "="*70)
    print("🎉 ALL TESTS PASSED SUCCESSFULLY!")
    print("="*70)
    print("✅ Multi-unit pricing product creation")
    print("✅ Unit selection in sales orders (box)")
    print("✅ Unit persistence in database")
    print("✅ Price calculation per unit")
    print("✅ Multiple unit types (carton)")
    print("="*70 + "\n")
    
    return True

def test_legacy_product_fallback():
    """Test products without pricing array (legacy mode)"""
    print("\n" + "="*70)
    print("🔄 LEGACY PRODUCT FALLBACK TEST")
    print("="*70)
    
    legacy_product = {
        "sku": f"LEGACY-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "name": "Legacy Product Without Pricing Array",
        "unit": "each",
        "selling_price": 25.00,
        "retail_price": 30.00,
        "wholesale_price": 22.00
    }
    
    try:
        response = requests.post(f"{BASE_URL}/api/products/", json=legacy_product, timeout=10)
        response.raise_for_status()
        product_id = response.json()["data"]["id"]
        
        print(f"✅ Legacy product created: {product_id}")
        print(f"   (No explicit pricing array - using legacy fields)")
        return True
        
    except Exception as e:
        print(f"❌ Legacy fallback test failed: {e}")
        return False

if __name__ == "__main__":
    print("\n🚀 Starting Multi-Unit Pricing Test Suite...")
    print(f"📍 Target server: {BASE_URL}")
    print(f"⏰ Test time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    
    # Check server availability
    try:
        response = requests.get(f"{BASE_URL}/api/products/", timeout=5)
        print(f"✅ Server is responding (Status: {response.status_code})\n")
    except Exception as e:
        print(f"❌ Cannot connect to server at {BASE_URL}")
        print(f"   Error: {e}")
        print(f"\n💡 Make sure the backend server is running:")
        print(f"   cd backend && python -m uvicorn app.main:app --reload\n")
        sys.exit(1)
    
    # Run tests
    success = test_multi_unit_pricing()
    
    if success:
        print("\n🔄 Running legacy fallback test...")
        test_legacy_product_fallback()
    
    sys.exit(0 if success else 1)
