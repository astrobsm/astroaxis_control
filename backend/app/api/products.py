from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, and_, or_
from sqlalchemy.orm import selectinload
from typing import List, Optional
from decimal import Decimal
import uuid

from app.db import get_session
from app.models import Product, ProductPricing, StockLevel, StockMovement, Warehouse
from app.schemas import (
    ProductSchema,
    ProductCreate,
    ProductUpdate,
    PaginatedResponse,
    ApiResponse
)

router = APIRouter(prefix='/api/products', tags=['Products'])

@router.post('/', response_model=ApiResponse)
async def create_product(
    product_data: ProductCreate,
    session: AsyncSession = Depends(get_session)
):
    """Create a new product"""
    # Check for duplicate SKU
    existing = await session.execute(
        select(Product).where(Product.sku == product_data.sku)
    )
    if existing.scalar():
        raise HTTPException(status_code=400, detail=f"Product with SKU '{product_data.sku}' already exists")
    
    # Extract pricing data
    pricing_data = product_data.pricing if hasattr(product_data, 'pricing') else []
    product_dict = product_data.model_dump(exclude={'pricing'})
    
    new_product = Product(**product_dict)
    session.add(new_product)
    await session.flush()  # Get the product ID before adding pricing
    
    # Add pricing entries
    if pricing_data:
        for pricing_item in pricing_data:
            # Convert Pydantic model to dict if needed
            pricing_dict = pricing_item.model_dump() if hasattr(pricing_item, 'model_dump') else pricing_item
            new_pricing = ProductPricing(
                product_id=new_product.id,
                unit=pricing_dict['unit'],
                cost_price=pricing_dict['cost_price'],
                retail_price=pricing_dict['retail_price'],
                wholesale_price=pricing_dict['wholesale_price']
            )
            session.add(new_pricing)
    
    await session.commit()
    
    # Reload product with pricing relationship
    result = await session.execute(
        select(Product).where(Product.id == new_product.id).options(selectinload(Product.pricing))
    )
    new_product = result.scalar()
    
    product_schema = ProductSchema.model_validate(new_product)
    
    return ApiResponse(
        message=f"Product '{new_product.name}' created successfully",
        data=product_schema
    )

@router.get('/')
async def list_products(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=100),
    search: Optional[str] = Query(None, description="Search by SKU or name"),
    session: AsyncSession = Depends(get_session)
):
    """List products with pagination and search"""
    from sqlalchemy.orm import selectinload
    
    offset = (page - 1) * size
    
    query = select(Product).options(selectinload(Product.pricing))
    count_query = select(func.count(Product.id))
    
    if search:
        search_filter = or_(
            Product.sku.ilike(f"%{search}%"),
            Product.name.ilike(f"%{search}%")
        )
        query = query.where(search_filter)
        count_query = count_query.where(search_filter)
    
    # Get total count
    total_result = await session.execute(count_query)
    total = total_result.scalar()
    
    # Get paginated results
    query = query.offset(offset).limit(size).order_by(Product.created_at.desc())
    result = await session.execute(query)
    products = result.scalars().all()
    
    items = []
    for p in products:
        schema_obj = ProductSchema.model_validate(p)
        item_dict = schema_obj.model_dump()
        # Convert UUID to string for JSON serialization
        item_dict['id'] = str(item_dict['id'])
        if item_dict['created_at']:
            item_dict['created_at'] = item_dict['created_at'].isoformat()
        items.append(item_dict)
    
    return {
        "items": items,
        "total": total,
        "page": page,
        "size": size,
        "pages": (total + size - 1) // size
    }

@router.get('/{product_id}', response_model=ApiResponse)
async def get_product(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_session)
):
    """Get a specific product by ID"""
    result = await session.execute(
        select(Product)
        .options(selectinload(Product.pricing))
        .where(Product.id == product_id)
    )
    product = result.scalar()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    return ApiResponse(
        message="Product retrieved successfully",
        data=ProductSchema.model_validate(product)
    )

@router.put('/{product_id}', response_model=ApiResponse)
async def update_product(
    product_id: uuid.UUID,
    product_data: ProductUpdate,
    session: AsyncSession = Depends(get_session)
):
    """Update a product"""
    result = await session.execute(
        select(Product)
        .options(selectinload(Product.pricing))
        .where(Product.id == product_id)
    )
    product = result.scalar()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Check for duplicate SKU if updating
    if product_data.sku and product_data.sku != product.sku:
        existing = await session.execute(
            select(Product).where(and_(Product.sku == product_data.sku, Product.id != product_id))
        )
        if existing.scalar():
            raise HTTPException(status_code=400, detail=f"Product with SKU '{product_data.sku}' already exists")
    
    # Handle pricing updates if provided
    pricing_data = None
    if hasattr(product_data, 'pricing') and product_data.pricing is not None:
        pricing_data = product_data.pricing
        # Delete existing pricing
        await session.execute(
            select(ProductPricing).where(ProductPricing.product_id == product_id)
        )
        existing_pricing = await session.execute(
            select(ProductPricing).where(ProductPricing.product_id == product_id)
        )
        for pricing in existing_pricing.scalars():
            await session.delete(pricing)
        
        # Add new pricing
        if pricing_data:
            for pricing_item in pricing_data:
                # Convert Pydantic model to dict if needed
                pricing_dict = pricing_item.model_dump() if hasattr(pricing_item, 'model_dump') else pricing_item
                new_pricing = ProductPricing(
                    product_id=product_id,
                    unit=pricing_dict['unit'],
                    cost_price=pricing_dict['cost_price'],
                    retail_price=pricing_dict['retail_price'],
                    wholesale_price=pricing_dict['wholesale_price']
                )
                session.add(new_pricing)
    
    # Update fields
    update_data = product_data.model_dump(exclude_unset=True, exclude={'pricing'})
    for field, value in update_data.items():
        setattr(product, field, value)
    
    await session.commit()
    await session.refresh(product)
    
    return ApiResponse(
        message=f"Product '{product.name}' updated successfully",
        data=ProductSchema.model_validate(product)
    )

@router.delete('/{product_id}', response_model=ApiResponse)
async def delete_product(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_session)
):
    """Delete a product (soft delete if has stock movements)"""
    result = await session.execute(
        select(Product).where(Product.id == product_id)
    )
    product = result.scalar()
    
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")
    
    # Check if product has stock movements
    movements_result = await session.execute(
        select(func.count(StockMovement.id)).where(StockMovement.product_id == product_id)
    )
    has_movements = movements_result.scalar() > 0
    
    if has_movements:
        raise HTTPException(
            status_code=400, 
            detail="Cannot delete product with existing stock movements. Archive it instead."
        )
    
    await session.delete(product)
    await session.commit()
    
    return ApiResponse(message=f"Product '{product.name}' deleted successfully")

@router.get('/{product_id}/stock', response_model=List[dict])
async def get_product_stock(
    product_id: uuid.UUID,
    session: AsyncSession = Depends(get_session)
):
    """Get stock levels for a product across all warehouses"""
    result = await session.execute(
        select(StockLevel, Warehouse)
        .join(Warehouse, StockLevel.warehouse_id == Warehouse.id)
        .where(and_(StockLevel.product_id == product_id, Warehouse.is_active == True))
    )
    
    stock_data = []
    for stock_level, warehouse in result:
        stock_data.append({
            "warehouse": {
                "id": warehouse.id,
                "code": warehouse.code,
                "name": warehouse.name
            },
            "current_stock": stock_level.current_stock,
            "reserved_stock": stock_level.reserved_stock,
            "available_stock": stock_level.current_stock - stock_level.reserved_stock,
            "min_stock": stock_level.min_stock,
            "max_stock": stock_level.max_stock,
            "stock_status": "LOW" if stock_level.current_stock <= stock_level.min_stock else 
                          "HIGH" if stock_level.current_stock >= stock_level.max_stock else "NORMAL"
        })
    
    return stock_data
