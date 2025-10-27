#!/usr/bin/env python3
"""
Manual testing script for CRUD APIs
"""
import asyncio
import requests
import json
import time

BASE_URL = "http://127.0.0.1:8001"

def test_health():
    """Test health endpoint"""
    try:
        response = requests.get(f"{BASE_URL}/api/health", timeout=5)
        print(f"✅ Health check: {response.status_code} - {response.json()}")
        return True
    except Exception as e:
        print(f"❌ Health check failed: {e}")
        return False

def test_warehouses():
    """Test warehouses CRUD"""
    print("\n🏭 Testing Warehouses API...")
    
    try:
        # List warehouses
        response = requests.get(f"{BASE_URL}/api/warehouses/")
        print(f"📋 List warehouses: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"   Total: {data.get('total', 0)} warehouses")
            print(f"   Items: {len(data.get('data', []))}")
            return data.get('data', [])
        return []
    except Exception as e:
        print(f"❌ Warehouses test failed: {e}")
        return []

def test_products():
    """Test products CRUD"""
    print("\n📦 Testing Products API...")
    
    try:
        # List products
        response = requests.get(f"{BASE_URL}/api/products/")
        print(f"📋 List products: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"   Total: {data.get('total', 0)} products")
            print(f"   Items: {len(data.get('data', []))}")
            return data.get('data', [])
        return []
    except Exception as e:
        print(f"❌ Products test failed: {e}")
        return []

def test_raw_materials():
    """Test raw materials CRUD"""
    print("\n🧱 Testing Raw Materials API...")
    
    try:
        # List raw materials
        response = requests.get(f"{BASE_URL}/api/raw-materials/")
        print(f"📋 List raw materials: {response.status_code}")
        if response.status_code == 200:
            data = response.json()
            print(f"   Total: {data.get('total', 0)} raw materials")
            print(f"   Items: {len(data.get('data', []))}")
            return data.get('data', [])
        return []
    except Exception as e:
        print(f"❌ Raw materials test failed: {e}")
        return []

def test_bom_cost():
    """Test BOM cost calculation"""
    print("\n💰 Testing BOM Cost Calculation...")
    
    try:
        # Get products first
        products_resp = requests.get(f"{BASE_URL}/api/products/")
        if products_resp.status_code == 200:
            products = products_resp.json().get('data', [])
            if products:
                product_id = products[0]['id']
                response = requests.get(f"{BASE_URL}/api/bom/{product_id}/cost")
                print(f"💰 BOM cost for {products[0]['name']}: {response.status_code}")
                if response.status_code == 200:
                    cost_data = response.json()
                    print(f"   Total cost: ${cost_data.get('total_cost', 'N/A')}")
                    return True
        return False
    except Exception as e:
        print(f"❌ BOM cost test failed: {e}")
        return False

def main():
    print("🚀 ASTRO-ASIX ERP - CRUD API Manual Testing")
    print("=" * 50)
    
    # Test server connection
    if not test_health():
        print("\n❌ Server is not running. Please start it with:")
        print("   uvicorn app.main:app --host 127.0.0.1 --port 8001")
        return
    
    # Run tests
    warehouses = test_warehouses()
    products = test_products()
    raw_materials = test_raw_materials()
    test_bom_cost()
    
    print(f"\n📊 Summary:")
    print(f"   Warehouses: {len(warehouses)}")
    print(f"   Products: {len(products)}")
    print(f"   Raw Materials: {len(raw_materials)}")
    print("\n✅ Manual API testing completed!")

if __name__ == "__main__":
    main()