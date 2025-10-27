#!/usr/bin/env python3
"""
Test script for all ERP API modules
"""
import requests
import json
from datetime import datetime

BASE_URL = "http://127.0.0.1:8000"

def test_endpoints():
    """Test all the new ERP API endpoints"""
    print("🧪 Testing ASTRO-ASIX ERP API Endpoints\n")
    
    # Health check
    print("1. Testing Health Endpoint:")
    try:
        response = requests.get(f"{BASE_URL}/api/health")
        print(f"   Status: {response.status_code}")
        print(f"   Response: {response.json()}\n")
    except Exception as e:
        print(f"   ❌ Error: {e}\n")
    
    # Test Sales API
    print("2. Testing Sales API:")
    try:
        # Get customers
        response = requests.get(f"{BASE_URL}/api/sales/customers")
        print(f"   GET /api/sales/customers: {response.status_code}")
        if response.status_code == 200:
            customers = response.json()
            print(f"   Found {customers.get('total', 0)} customers")
        
        # Get sales orders  
        response = requests.get(f"{BASE_URL}/api/sales/orders")
        print(f"   GET /api/sales/orders: {response.status_code}")
        if response.status_code == 200:
            orders = response.json()
            print(f"   Found {orders.get('total', 0)} sales orders")
    except Exception as e:
        print(f"   ❌ Sales API Error: {e}")
    print()
    
    # Test Production API
    print("3. Testing Production API:")
    try:
        # Get production orders
        response = requests.get(f"{BASE_URL}/api/production/orders")
        print(f"   GET /api/production/orders: {response.status_code}")
        if response.status_code == 200:
            orders = response.json()
            print(f"   Found {orders.get('total', 0)} production orders")
        
        # Get dashboard stats
        response = requests.get(f"{BASE_URL}/api/production/dashboard/stats")
        print(f"   GET /api/production/dashboard/stats: {response.status_code}")
        if response.status_code == 200:
            stats = response.json()
            print(f"   Production Stats: {stats}")
    except Exception as e:
        print(f"   ❌ Production API Error: {e}")
    print()
    
    # Test Staff API
    print("4. Testing Staff API:")
    try:
        # Get departments
        response = requests.get(f"{BASE_URL}/api/staff/departments")
        print(f"   GET /api/staff/departments: {response.status_code}")
        if response.status_code == 200:
            deps = response.json()
            print(f"   Found {deps.get('total', 0)} departments")
        
        # Get employees
        response = requests.get(f"{BASE_URL}/api/staff/employees")
        print(f"   GET /api/staff/employees: {response.status_code}")
        if response.status_code == 200:
            emps = response.json()
            print(f"   Found {emps.get('total', 0)} employees")
        
        # Get staff stats
        response = requests.get(f"{BASE_URL}/api/staff/dashboard/stats")
        print(f"   GET /api/staff/dashboard/stats: {response.status_code}")
        if response.status_code == 200:
            stats = response.json()
            print(f"   Staff Stats: {stats}")
    except Exception as e:
        print(f"   ❌ Staff API Error: {e}")
    print()
    
    # Test existing APIs
    print("5. Testing Existing APIs:")
    try:
        # Products
        response = requests.get(f"{BASE_URL}/api/products")
        print(f"   GET /api/products: {response.status_code}")
        
        # Raw Materials
        response = requests.get(f"{BASE_URL}/api/raw-materials")
        print(f"   GET /api/raw-materials: {response.status_code}")
        
        # Warehouses
        response = requests.get(f"{BASE_URL}/api/warehouses")
        print(f"   GET /api/warehouses: {response.status_code}")
        
        # Stock
        response = requests.get(f"{BASE_URL}/api/stock/levels")
        print(f"   GET /api/stock/levels: {response.status_code}")
        
        # BOM
        response = requests.get(f"{BASE_URL}/api/bom/cost/products")
        print(f"   GET /api/bom/cost/products: {response.status_code}")
        
    except Exception as e:
        print(f"   ❌ Existing API Error: {e}")
    print()
    
    print("✅ API Testing Complete!")

if __name__ == '__main__':
    test_endpoints()