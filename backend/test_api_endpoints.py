#!/usr/bin/env python3
"""
Comprehensive API endpoint testing script
"""
import requests
import json
import sys

BASE_URL = "http://localhost:8000"

def test_endpoint(method, endpoint, expected_status=200, data=None):
    """Test a single endpoint"""
    url = f"{BASE_URL}{endpoint}"
    try:
        if method.upper() == "GET":
            response = requests.get(url, timeout=10)
        elif method.upper() == "POST":
            response = requests.post(url, json=data, timeout=10)
        elif method.upper() == "PUT":
            response = requests.put(url, json=data, timeout=10)
        elif method.upper() == "DELETE":
            response = requests.delete(url, timeout=10)
        else:
            print(f"❌ Unknown method: {method}")
            return False
        
        if response.status_code == expected_status:
            print(f"✅ {method} {endpoint} - Status: {response.status_code}")
            return True
        else:
            print(f"❌ {method} {endpoint} - Expected: {expected_status}, Got: {response.status_code}")
            if response.text:
                print(f"   Response: {response.text[:200]}...")
            return False
    except requests.exceptions.RequestException as e:
        print(f"❌ {method} {endpoint} - Request failed: {e}")
        return False
    except Exception as e:
        print(f"❌ {method} {endpoint} - Error: {e}")
        return False

def main():
    """Test all API endpoints"""
    print("🔍 ASTRO-ASIX ERP - API Endpoint Testing")
    print("=" * 50)
    
    # Define all endpoints to test
    endpoints = [
        # Basic endpoints
        ("GET", "/", 404),  # Root should return 404 or redirect
        ("GET", "/api/", 404),  # API root
        
        # Products endpoints
        ("GET", "/api/products/", 200),
        ("GET", "/api/raw-materials/", 200),
        ("GET", "/api/warehouses/", 200),
        
        # Sales endpoints
        ("GET", "/api/sales/customers/", 200),
        ("GET", "/api/sales/orders/", 200),
        
        # Production endpoints
        ("GET", "/api/production/orders/", 200),
        
        # Staff endpoints
        ("GET", "/api/staff/departments/", 200),
        ("GET", "/api/staff/employees/", 200),
        
        # Stock endpoints
        ("GET", "/api/stock/movements/", 200),
        ("GET", "/api/stock/levels/", 200),
        
        # BOM endpoints
        ("GET", "/api/bom/", 200),
        
        # Debug endpoints
        ("GET", "/api/debug/health", 200),
    ]
    
    print(f"🧪 Testing {len(endpoints)} endpoints...")
    print()
    
    passed = 0
    total = len(endpoints)
    
    for method, endpoint, expected_status in endpoints:
        if test_endpoint(method, endpoint, expected_status):
            passed += 1
    
    print()
    print("=" * 50)
    print("📋 API TEST SUMMARY")
    print("=" * 50)
    print(f"🎯 Results: {passed}/{total} endpoints passed")
    
    if passed == total:
        print("🎉 All API endpoints are working correctly!")
        return 0
    else:
        print("⚠️  Some endpoints failed. Check the errors above.")
        return 1

if __name__ == "__main__":
    result = main()
    sys.exit(result)