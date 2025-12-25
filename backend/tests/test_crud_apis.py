import pytest
import pytest_asyncio
import sys
import os
import uuid
from httpx import AsyncClient
from sqlalchemy import text
from decimal import Decimal

# Ensure we import the local app package
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import main as main_mod
from app import db as db_mod
from app import models as models_mod
from app.api import auth as auth_mod

engine = db_mod.engine
Base = models_mod.Base
app = main_mod.app


@pytest_asyncio.fixture(scope="function", autouse=True)
async def setup_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


async def create_test_user_headers(role: str = "admin") -> dict:
    """Create a test user directly in the DB and return auth headers."""
    email = f"test_{uuid.uuid4().hex}@example.com"
    password = "TestPass123!"

    async with db_mod.AsyncSessionLocal() as session:
        user_id = uuid.uuid4()
        user = models_mod.User(
            id=user_id,
            email=email,
            hashed_password=auth_mod.hash_password(password),
            full_name="Test User",
            role=role,
            is_active=True,
            is_locked=False,
        )
        session.add(user)
        await session.commit()

    token = auth_mod.create_access_token(
        data={"sub": str(user_id), "email": email, "role": role}
    )
    return {"Authorization": f"Bearer {token}"}

@pytest.mark.asyncio
async def test_products_crud():
    """Test complete CRUD operations for products"""
    # Ensure DB is ready
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with AsyncClient(app=app, base_url='http://test') as client:
        # Test CREATE product
        product_data = {
            "sku": "TEST-PROD-001",
            "name": "Test Product",
            "description": "A test product for API testing",
            "unit": "piece"
        }
        
        response = await client.post('/api/products/', json=product_data)
        assert response.status_code == 200
        result = response.json()
        assert result['success'] == True
        product_id = result['data']['id']
        assert result['data']['sku'] == 'TEST-PROD-001'
        
        # Test READ product
        response = await client.get(f'/api/products/{product_id}')
        assert response.status_code == 200
        result = response.json()
        assert result['data']['name'] == 'Test Product'
        
        # Test LIST products
        response = await client.get('/api/products/')
        assert response.status_code == 200
        result = response.json()
        assert result['total'] >= 1
        
        # Test UPDATE product
        update_data = {
            "name": "Updated Test Product",
            "description": "Updated description"
        }
        response = await client.put(f'/api/products/{product_id}', json=update_data)
        assert response.status_code == 200
        result = response.json()
        assert result['data']['name'] == 'Updated Test Product'
        
        # Test duplicate SKU prevention
        duplicate_data = {"sku": "TEST-PROD-001", "name": "Duplicate"}
        response = await client.post('/api/products/', json=duplicate_data)
        assert response.status_code == 400

@pytest.mark.asyncio
async def test_raw_materials_crud():
    """Test CRUD operations for raw materials"""
    async with AsyncClient(app=app, base_url='http://test') as client:
        # Test CREATE raw material
        material_data = {
            "sku": "TEST-RM-001",
            "name": "Test Raw Material",
            "unit_cost": "1.50"
        }
        
        response = await client.post('/api/raw-materials/', json=material_data)
        assert response.status_code == 200
        result = response.json()
        assert result['success'] == True
        material_id = result['data']['id']
        
        # Test READ
        response = await client.get(f'/api/raw-materials/{material_id}')
        assert response.status_code == 200
        result = response.json()
        assert Decimal(result['data']['unit_cost']) == Decimal('1.50')
        
        # Test LIST with search
        response = await client.get('/api/raw-materials/?search=Test')
        assert response.status_code == 200
        result = response.json()
        assert len(result['items']) >= 1

@pytest.mark.asyncio
async def test_warehouses_crud():
    """Test CRUD operations for warehouses"""
    async with AsyncClient(app=app, base_url='http://test') as client:
        headers = await create_test_user_headers()

        # Test CREATE warehouse
        warehouse_data = {
            "code": "TEST-WH",
            "name": "Test Warehouse",
            "location": "Test Location"
        }
        
        response = await client.post(
            '/api/warehouses/',
            json=warehouse_data,
            headers=headers,
        )
        assert response.status_code == 200
        result = response.json()
        warehouse_id = result['data']['id']
        
        # Test READ
        response = await client.get(
            f'/api/warehouses/{warehouse_id}',
            headers=headers,
        )
        assert response.status_code == 200
        
        # Test UPDATE
        update_data = {"location": "Updated Location"}
        response = await client.put(
            f'/api/warehouses/{warehouse_id}',
            json=update_data,
            headers=headers,
        )
        assert response.status_code == 200
        
        # Test warehouse summary
        response = await client.get(
            f'/api/warehouses/{warehouse_id}/summary',
            headers=headers,
        )
        assert response.status_code == 200
        result = response.json()
        assert 'stock_summary' in result

        # Test DELETE
        response = await client.delete(
            f'/api/warehouses/{warehouse_id}',
            headers=headers,
            params={'force': True},
        )
        assert response.status_code == 200

@pytest.mark.asyncio
async def test_stock_movements():
    """Test stock movement operations"""
    async with AsyncClient(app=app, base_url='http://test') as client:
        headers = await create_test_user_headers()

        # First ensure we have a warehouse and raw material
        # Get existing warehouse
        response = await client.get('/api/warehouses/', headers=headers)
        warehouses = response.json()
        if warehouses['total'] == 0:
            # Create warehouse if none exists
            warehouse_data = {
                "code": "STOCK-TEST",
                "name": "Stock Test Warehouse",
            }
            response = await client.post(
                '/api/warehouses/',
                json=warehouse_data,
                headers=headers,
            )
            warehouse_id = response.json()['data']['id']
        else:
            warehouse_id = warehouses['items'][0]['id']
        
        # Get existing raw material
        response = await client.get('/api/raw-materials/')
        materials = response.json()
        if materials['total'] == 0:
            # Create material if none exists
            material_data = {"sku": "STOCK-RM", "name": "Stock Test Material", "unit_cost": "2.00"}
            response = await client.post('/api/raw-materials/', json=material_data)
            material_id = response.json()['data']['id']
        else:
            material_id = materials['items'][0]['id']
        
        # Test stock IN movement
        movement_data = {
            "warehouse_id": warehouse_id,
            "raw_material_id": material_id,
            "movement_type": "IN",
            "quantity": "100.0",
            "unit_cost": "2.00",
            "reference": "TEST-001",
            "notes": "Test stock in"
        }
        
        response = await client.post(
            '/api/stock/movement/raw-material',
            json=movement_data,
        )
        assert response.status_code == 200
        result = response.json()
        assert result['success'] == True
        
        # Test list movements
        response = await client.get('/api/stock/movements')
        assert response.status_code == 200
        result = response.json()
        assert result['total'] >= 1
        
        # Test stock levels
        response = await client.get('/api/stock/levels')
        assert response.status_code == 200
        stock_levels = response.json()
        assert len(stock_levels) >= 1
        
        # Test inventory valuation
        response = await client.get('/api/stock/valuation')
        assert response.status_code == 200
        valuation = response.json()
        assert 'raw_materials' in valuation
        assert 'grand_total' in valuation

@pytest.mark.asyncio
async def test_api_error_handling():
    """Test API error handling"""
    async with AsyncClient(app=app, base_url='http://test') as client:
        # Test 404 for non-existent product
        fake_id = "550e8400-e29b-41d4-a716-446655440000"
        response = await client.get(f'/api/products/{fake_id}')
        assert response.status_code == 404
        
        # Test validation error
        invalid_product = {"sku": "", "name": ""}  # Empty required fields
        response = await client.post('/api/products/', json=invalid_product)
        assert response.status_code == 422  # Validation error