from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db import get_session
from app.models import Product
from app.schemas import ProductSchema

router = APIRouter(prefix='/api/debug', tags=['Debug'])

@router.get('/products-raw')
async def debug_products(session: AsyncSession = Depends(get_session)):
    """Debug endpoint to test product retrieval"""
    result = await session.execute(select(Product).limit(3))
    products = result.scalars().all()
    
    debug_data = []
    for p in products:
        try:
            # Test the conversion step by step
            raw_dict = {
                'id': str(p.id),
                'sku': p.sku,
                'name': p.name,
                'description': p.description,
                'unit': p.unit,
                'created_at': p.created_at.isoformat()
            }
            
            # Test Pydantic conversion
            schema_obj = ProductSchema.model_validate(p)
            schema_dict = schema_obj.model_dump()
            
            debug_data.append({
                'raw_attributes': raw_dict,
                'schema_conversion': schema_dict,
                'schema_obj_str': str(schema_obj)
            })
        except Exception as e:
            debug_data.append({
                'error': str(e),
                'product_id': str(p.id)
            })
    
    return {
        'count': len(products),
        'debug_data': debug_data
    }