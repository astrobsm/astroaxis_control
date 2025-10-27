from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.db import get_session
from app.models import Permission, UserPermission, User
from pydantic import BaseModel
from typing import List, Optional
import uuid
from datetime import datetime

router = APIRouter(prefix="/api/permissions", tags=["permissions"])

# Pydantic schemas
class PermissionCreate(BaseModel):
    name: str
    display_name: str
    description: Optional[str] = None
    module: str

class PermissionResponse(BaseModel):
    id: str
    name: str
    display_name: str
    description: Optional[str]
    module: str
    created_at: datetime

    class Config:
        from_attributes = True

class UserPermissionGrant(BaseModel):
    user_id: str
    permission_ids: List[str]

class UserPermissionResponse(BaseModel):
    user_id: str
    user_name: str
    user_email: str
    user_role: str
    permissions: List[PermissionResponse]

# Initialize default permissions
DEFAULT_PERMISSIONS = [
    {"name": "staff.view", "display_name": "View Staff", "module": "staff", "description": "View staff members"},
    {"name": "staff.create", "display_name": "Create Staff", "module": "staff", "description": "Add new staff members"},
    {"name": "staff.edit", "display_name": "Edit Staff", "module": "staff", "description": "Edit staff information"},
    {"name": "staff.delete", "display_name": "Delete Staff", "module": "staff", "description": "Delete staff members"},
    
    {"name": "attendance.view", "display_name": "View Attendance", "module": "attendance", "description": "View attendance records"},
    {"name": "attendance.manage", "display_name": "Manage Attendance", "module": "attendance", "description": "Clock in/out and manage attendance"},
    
    {"name": "sales.view", "display_name": "View Sales", "module": "sales", "description": "View sales orders"},
    {"name": "sales.create", "display_name": "Create Sales", "module": "sales", "description": "Create sales orders"},
    {"name": "sales.edit", "display_name": "Edit Sales", "module": "sales", "description": "Edit sales orders"},
    {"name": "sales.delete", "display_name": "Delete Sales", "module": "sales", "description": "Delete sales orders"},
    
    {"name": "production.view", "display_name": "View Production", "module": "production", "description": "View production orders"},
    {"name": "production.create", "display_name": "Create Production", "module": "production", "description": "Create production orders"},
    {"name": "production.edit", "display_name": "Edit Production", "module": "production", "description": "Edit production orders"},
    
    {"name": "inventory.view", "display_name": "View Inventory", "module": "inventory", "description": "View inventory/stock"},
    {"name": "inventory.manage", "display_name": "Manage Inventory", "module": "inventory", "description": "Manage stock movements"},
    
    {"name": "products.view", "display_name": "View Products", "module": "products", "description": "View products"},
    {"name": "products.create", "display_name": "Create Products", "module": "products", "description": "Add new products"},
    {"name": "products.edit", "display_name": "Edit Products", "module": "products", "description": "Edit products"},
    
    {"name": "warehouse.view", "display_name": "View Warehouses", "module": "warehouse", "description": "View warehouses"},
    {"name": "warehouse.manage", "display_name": "Manage Warehouses", "module": "warehouse", "description": "Create/edit warehouses"},
    
    {"name": "settings.view", "display_name": "View Settings", "module": "settings", "description": "View system settings"},
    {"name": "settings.manage", "display_name": "Manage Settings", "module": "settings", "description": "Change system settings"},
    
    {"name": "bom.view", "display_name": "View BOM", "module": "bom", "description": "View Bill of Materials"},
    {"name": "bom.manage", "display_name": "Manage BOM", "module": "bom", "description": "Create/edit Bill of Materials"},
]

@router.post("/initialize", status_code=status.HTTP_201_CREATED)
async def initialize_permissions(session: AsyncSession = Depends(get_session)):
    """Initialize default permissions in the database"""
    try:
        created_count = 0
        for perm_data in DEFAULT_PERMISSIONS:
            # Check if permission already exists
            result = await session.execute(
                select(Permission).where(Permission.name == perm_data["name"])
            )
            existing = result.scalar_one_or_none()
            
            if not existing:
                permission = Permission(
                    id=uuid.uuid4(),
                    name=perm_data["name"],
                    display_name=perm_data["display_name"],
                    description=perm_data["description"],
                    module=perm_data["module"]
                )
                session.add(permission)
                created_count += 1
        
        await session.commit()
        return {"message": f"Initialized {created_count} permissions", "total": len(DEFAULT_PERMISSIONS)}
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/", response_model=List[PermissionResponse])
async def get_all_permissions(session: AsyncSession = Depends(get_session)):
    """Get all available permissions"""
    result = await session.execute(select(Permission))
    permissions = result.scalars().all()
    return permissions

@router.get("/modules")
async def get_modules(session: AsyncSession = Depends(get_session)):
    """Get all unique modules with their permissions"""
    result = await session.execute(select(Permission))
    permissions = result.scalars().all()
    
    modules = {}
    for perm in permissions:
        if perm.module not in modules:
            modules[perm.module] = []
        modules[perm.module].append({
            "id": str(perm.id),
            "name": perm.name,
            "display_name": perm.display_name,
            "description": perm.description
        })
    
    return modules

@router.get("/user/{user_id}", response_model=UserPermissionResponse)
async def get_user_permissions(user_id: str, session: AsyncSession = Depends(get_session)):
    """Get all permissions for a specific user"""
    try:
        # Get user
        result = await session.execute(select(User).where(User.id == uuid.UUID(user_id)))
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Get user permissions
        result = await session.execute(
            select(UserPermission, Permission)
            .join(Permission, UserPermission.permission_id == Permission.id)
            .where(UserPermission.user_id == uuid.UUID(user_id))
        )
        rows = result.all()
        
        permissions = [PermissionResponse(
            id=str(row[1].id),
            name=row[1].name,
            display_name=row[1].display_name,
            description=row[1].description,
            module=row[1].module,
            created_at=row[1].created_at
        ) for row in rows]
        
        return UserPermissionResponse(
            user_id=str(user.id),
            user_name=user.full_name,
            user_email=user.email,
            user_role=user.role,
            permissions=permissions
        )
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/grant", status_code=status.HTTP_201_CREATED)
async def grant_permissions(
    grant_data: UserPermissionGrant,
    admin_user_id: str,  # Should come from JWT token in production
    session: AsyncSession = Depends(get_session)
):
    """Grant permissions to a user (Admin only)"""
    try:
        # Verify user exists
        result = await session.execute(
            select(User).where(User.id == uuid.UUID(grant_data.user_id))
        )
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        # Delete existing permissions for this user
        await session.execute(
            delete(UserPermission).where(UserPermission.user_id == uuid.UUID(grant_data.user_id))
        )
        
        # Grant new permissions
        granted_count = 0
        for perm_id in grant_data.permission_ids:
            # Verify permission exists
            result = await session.execute(
                select(Permission).where(Permission.id == uuid.UUID(perm_id))
            )
            permission = result.scalar_one_or_none()
            
            if permission:
                user_permission = UserPermission(
                    id=uuid.uuid4(),
                    user_id=uuid.UUID(grant_data.user_id),
                    permission_id=uuid.UUID(perm_id),
                    granted_by=uuid.UUID(admin_user_id) if admin_user_id else None
                )
                session.add(user_permission)
                granted_count += 1
        
        await session.commit()
        return {"message": f"Granted {granted_count} permissions to user", "user_id": grant_data.user_id}
    except ValueError:
        await session.rollback()
        raise HTTPException(status_code=400, detail="Invalid UUID format")
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/user/{user_id}/permission/{permission_id}")
async def revoke_permission(
    user_id: str,
    permission_id: str,
    session: AsyncSession = Depends(get_session)
):
    """Revoke a specific permission from a user"""
    try:
        result = await session.execute(
            delete(UserPermission)
            .where(UserPermission.user_id == uuid.UUID(user_id))
            .where(UserPermission.permission_id == uuid.UUID(permission_id))
        )
        
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Permission not found for this user")
        
        await session.commit()
        return {"message": "Permission revoked successfully"}
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid UUID format")
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/check/{user_id}/{permission_name}")
async def check_permission(
    user_id: str,
    permission_name: str,
    session: AsyncSession = Depends(get_session)
):
    """Check if a user has a specific permission"""
    try:
        result = await session.execute(
            select(UserPermission)
            .join(Permission, UserPermission.permission_id == Permission.id)
            .where(UserPermission.user_id == uuid.UUID(user_id))
            .where(Permission.name == permission_name)
        )
        
        has_permission = result.scalar_one_or_none() is not None
        
        return {
            "user_id": user_id,
            "permission": permission_name,
            "has_permission": has_permission
        }
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user ID")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
