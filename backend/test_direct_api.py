#!/usr/bin/env python3
"""
Direct API endpoint testing without server
"""
import asyncio
from fastapi.testclient import TestClient
from app.main import app

def test_api_endpoints():
    """Test API endpoints directly"""
    print("🌐 Testing API Endpoints Directly")
    print("=" * 40)
    
    client = TestClient(app)
    
    # Test health endpoint
    print("\n🏥 Health Check:")
    response = client.get("/api/health")
    print(f"   Status: {response.status_code}")
    print(f"   Response: {response.json()}")
    
    # Test products endpoint
    print("\n📦 Products API:")
    response = client.get("/api/products/")
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   Total Products: {data.get('total', 'N/A')}")
        print(f"   Items in Response: {len(data.get('items', []))}")
        if data.get('items'):
            first_item = data['items'][0]
            print(f"   First Item Keys: {list(first_item.keys()) if first_item else 'Empty'}")
    
    # Test warehouses endpoint  
    print("\n🏭 Warehouses API:")
    response = client.get("/api/warehouses/")
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   Total Warehouses: {data.get('total', 'N/A')}")
        print(f"   Items in Response: {len(data.get('items', []))}")
    
    # Test raw materials endpoint
    print("\n🧱 Raw Materials API:")
    response = client.get("/api/raw-materials/")
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   Total Raw Materials: {data.get('total', 'N/A')}")
        print(f"   Items in Response: {len(data.get('items', []))}")
    
    # Test BOM cost endpoint
    print("\n💰 BOM Cost API:")
    # Use known product ID
    product_id = "3f6089f6-55e3-4d4d-89f1-f971b76aed0d"  # Wound Dressing
    response = client.get(f"/api/bom/{product_id}/cost")
    print(f"   Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        print(f"   BOM Cost: ${data.get('total_cost', 'N/A')}")
    else:
        print(f"   Error: {response.text}")
    
    print(f"\n✅ API Testing Complete!")

if __name__ == "__main__":
    test_api_endpoints()