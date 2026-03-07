from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.db import get_session
from app.models import User, UserSession, AuditLog, RolePermission, UserModuleAccess
from pydantic import BaseModel, EmailStr
from typing import Optional
from uuid import UUID
import uuid
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from jose import jwt, JWTError

router = APIRouter(prefix="/api/auth", tags=["authentication"])

# Configuration
SECRET_KEY = "astroasix-secret-key-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 480  # 8 hours

# Pydantic schemas
class UserRegister(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: str = "sales_staff"
    phone: Optional[str] = None
    department: Optional[str] = None

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict

class PasswordChange(BaseModel):
    old_password: str
    new_password: str

# Utility functions
def hash_password(password: str) -> str:
    """Hash password using SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hash"""
    return hash_password(plain_password) == hashed_password

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def decode_token(token: str) -> dict:
    """Decode JWT token"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# Register new user
@router.post("/register")
async def register(user_data: UserRegister, db: AsyncSession = Depends(get_session)):
    """Register a new user"""
    result = await db.execute(select(User).where(User.email == user_data.email))
    existing_user = result.scalar_one_or_none()
    
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    valid_roles = ["admin", "sales_staff", "marketer", "customer_care", "production_staff", "warehouse_logistics"]
    if user_data.role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {', '.join(valid_roles)}")
    
    new_user = User(
        id=uuid.uuid4(),
        email=user_data.email,
        hashed_password=hash_password(user_data.password),
        full_name=user_data.full_name,
        role=user_data.role,
        phone=user_data.phone,
        department=user_data.department,
        is_active=False,  # Users start as pending approval
        is_locked=False,
        failed_login_attempts=0,
        two_factor_enabled=False
    )
    
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    
    # Create audit log AFTER user is committed
    audit_log = AuditLog(
        id=uuid.uuid4(),
        user_id=new_user.id,
        action="USER_REGISTERED",
        module="auth",
        details=f"New user registered: {user_data.email} with role {user_data.role}"
    )
    db.add(audit_log)
    await db.commit()
    
    return {
        "success": True,
        "message": "Registration successful! Your account is pending approval by an administrator.",
        "user": {
            "id": str(new_user.id),
            "email": new_user.email,
            "full_name": new_user.full_name,
            "role": new_user.role
        }
    }

# Login
@router.post("/login", response_model=TokenResponse)
async def login(credentials: UserLogin, db: AsyncSession = Depends(get_session)):
    """Authenticate user and return access token"""
    result = await db.execute(select(User).where(User.email == credentials.email))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    if user.is_locked:
        raise HTTPException(status_code=403, detail="Account is locked. Contact administrator.")
    
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is inactive. Contact administrator.")
    
    if not verify_password(credentials.password, user.hashed_password):
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        
        if user.failed_login_attempts >= 5:
            user.is_locked = True
            await db.commit()
            raise HTTPException(status_code=403, detail="Account locked due to too many failed login attempts")
        
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")
    
    user.failed_login_attempts = 0
    user.last_login = datetime.now(timezone.utc)
    
    access_token = create_access_token(
        data={"sub": str(user.id), "email": user.email, "role": user.role}
    )
    
    session_token = secrets.token_urlsafe(32)
    user_session = UserSession(
        id=uuid.uuid4(),
        user_id=user.id,
        token=session_token,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    db.add(user_session)
    
    audit_log = AuditLog(
        id=uuid.uuid4(),
        user_id=user.id,
        action="USER_LOGIN",
        module="auth",
        details=f"User logged in: {user.email}"
    )
    db.add(audit_log)
    
    await db.commit()
    
    # Fetch module access for user
    access_result = await db.execute(
        select(UserModuleAccess).where(UserModuleAccess.user_id == user.id)
    )
    access_rows = access_result.scalars().all()
    access_map = {r.module_key: r.is_granted for r in access_rows}
    is_admin = user.role == 'admin'
    all_mods = [
        'dashboard','staff','attendance','products','rawMaterials','stockManagement',
        'production','productionCompletions','consumables','machinesEquipment','sales',
        'paymentTracking','procurement','logistics','marketing','hrCustomerCare',
        'reports','financial','userManagement','settings'
    ]
    module_access = {m: access_map.get(m, True if is_admin else (m == 'dashboard')) for m in all_mods}
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "department": user.department,
            "phone": user.phone,
            "module_access": module_access
        }
    }

# Phone-based Login
class PhoneLogin(BaseModel):
    phone: str
    password: str
    role: str

@router.post("/login-phone", response_model=TokenResponse)
async def login_phone(credentials: PhoneLogin, db: AsyncSession = Depends(get_session)):
    """Authenticate user by phone number, password, and role"""
    result = await db.execute(
        select(User).where(
            User.phone == credentials.phone,
            User.role == credentials.role
        )
    )
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=401, detail="Invalid phone, password, or role")
    
    if user.is_locked:
        raise HTTPException(status_code=403, detail="Account is locked. Contact administrator.")
    
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is inactive. Contact administrator.")
    
    if not verify_password(credentials.password, user.hashed_password):
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        
        if user.failed_login_attempts >= 5:
            user.is_locked = True
            await db.commit()
            raise HTTPException(status_code=403, detail="Account locked due to too many failed login attempts")
        
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid phone, password, or role")
    
    user.failed_login_attempts = 0
    user.last_login = datetime.now(timezone.utc)
    
    access_token = create_access_token(
        data={"sub": str(user.id), "email": user.email, "role": user.role}
    )
    
    session_token = secrets.token_urlsafe(32)
    user_session = UserSession(
        id=uuid.uuid4(),
        user_id=user.id,
        token=session_token,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    db.add(user_session)
    
    audit_log = AuditLog(
        id=uuid.uuid4(),
        user_id=user.id,
        action="USER_LOGIN_PHONE",
        module="auth",
        details=f"User logged in via phone: {user.phone} with role {user.role}"
    )
    db.add(audit_log)
    
    await db.commit()
    
    # Fetch module access for user
    access_result = await db.execute(
        select(UserModuleAccess).where(UserModuleAccess.user_id == user.id)
    )
    access_rows = access_result.scalars().all()
    access_map = {r.module_key: r.is_granted for r in access_rows}
    is_admin = user.role == 'admin'
    all_mods = [
        'dashboard','staff','attendance','products','rawMaterials','stockManagement',
        'production','productionCompletions','consumables','machinesEquipment','sales',
        'paymentTracking','procurement','logistics','marketing','hrCustomerCare',
        'reports','financial','userManagement','settings'
    ]
    module_access = {m: access_map.get(m, True if is_admin else (m == 'dashboard')) for m in all_mods}
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "department": user.department,
            "phone": user.phone,
            "module_access": module_access
        }
    }

# Logout
class LogoutRequest(BaseModel):
    token: str

@router.post("/logout")
async def logout(request: LogoutRequest, db: AsyncSession = Depends(get_session)):
    """Logout user by invalidating session token"""
    result = await db.execute(select(UserSession).where(UserSession.token == request.token))
    session = result.scalar_one_or_none()
    
    if session:
        audit_log = AuditLog(
            id=uuid.uuid4(),
            user_id=session.user_id,
            action="USER_LOGOUT",
            module="auth",
            details="User logged out"
        )
        db.add(audit_log)
        
        await db.delete(session)
        await db.commit()
    
    return {"success": True, "message": "Logged out successfully"}

# Get current user
@router.get("/me")
async def get_current_user(token: str, db: AsyncSession = Depends(get_session)):
    """Get current user information from token"""
    try:
        payload = decode_token(token)
        user_id = payload.get("sub")
        
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        return {
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "department": user.department,
            "phone": user.phone,
            "is_active": user.is_active,
            "last_login": user.last_login.isoformat() if user.last_login else None
        }
    except Exception as e:
        raise HTTPException(status_code=401, detail=str(e))

# Change password
@router.post("/change-password")
async def change_password(
    password_data: PasswordChange,
    token: str,
    db: AsyncSession = Depends(get_session)
):
    """Change user password"""
    payload = decode_token(token)
    user_id = payload.get("sub")
    
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if not verify_password(password_data.old_password, user.hashed_password):
        raise HTTPException(status_code=400, detail="Old password is incorrect")
    
    user.hashed_password = hash_password(password_data.new_password)
    
    audit_log = AuditLog(
        id=uuid.uuid4(),
        user_id=user.id,
        action="PASSWORD_CHANGED",
        module="auth",
        details="User changed password"
    )
    db.add(audit_log)
    
    await db.commit()
    
    return {"success": True, "message": "Password changed successfully"}

# Check permissions
@router.post("/check-permission")
async def check_permission(
    module: str,
    action: str,
    token: str,
    db: AsyncSession = Depends(get_session)
):
    """Check if user has permission for module and action"""
    payload = decode_token(token)
    user_id = payload.get("sub")
    
    result = await db.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    if user.role == "admin":
        return {"has_permission": True}
    
    result = await db.execute(
        select(RolePermission).where(
            RolePermission.role == user.role,
            RolePermission.module == module
        )
    )
    permission = result.scalar_one_or_none()
    
    if not permission:
        return {"has_permission": False}
    
    action_map = {
        "view": permission.can_view,
        "create": permission.can_create,
        "edit": permission.can_edit,
        "delete": permission.can_delete,
        "approve": permission.can_approve
    }
    
    has_permission = action_map.get(action, False)
    
    return {"has_permission": has_permission}


# User Management Endpoints
@router.get("/users")
async def list_all_users(db: AsyncSession = Depends(get_session)):
    """Get all users with their status"""
    try:
        result = await db.execute(
            select(User).order_by(User.created_at.desc())
        )
        users = result.scalars().all()
        
        return [{
            "id": str(user.id),
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "department": user.department,
            "phone": user.phone,
            "is_active": user.is_active,
            "is_locked": user.is_locked,
            "created_at": user.created_at.isoformat() if user.created_at else None
        } for user in users]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching users: {str(e)}")


@router.post("/users/{user_id}/approve")
async def approve_user(user_id: UUID, db: AsyncSession = Depends(get_session)):
    """Approve a pending user registration"""
    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        user.is_active = True
        
        audit_log = AuditLog(
            id=uuid.uuid4(),
            user_id=user.id,
            action="USER_APPROVED",
            module="auth",
            details=f"User {user.email} approved by administrator"
        )
        db.add(audit_log)
        
        await db.commit()
        
        return {
            "success": True,
            "message": f"User {user.email} has been approved and activated"
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Error approving user: {str(e)}")


@router.post("/users/{user_id}/reject")
async def reject_user(user_id: UUID, db: AsyncSession = Depends(get_session)):
    """Reject and delete a pending user registration"""
    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        email = user.email
        
        await db.delete(user)
        await db.commit()
        
        return {
            "success": True,
            "message": f"User registration for {email} has been rejected and removed"
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Error rejecting user: {str(e)}")


@router.post("/users/{user_id}/toggle-lock")
async def toggle_user_lock(user_id: UUID, db: AsyncSession = Depends(get_session)):
    """Lock or unlock a user account"""
    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        user.is_locked = not user.is_locked
        action = "LOCKED" if user.is_locked else "UNLOCKED"
        
        audit_log = AuditLog(
            id=uuid.uuid4(),
            user_id=user.id,
            action=f"USER_{action}",
            module="auth",
            details=f"User {user.email} {action.lower()} by administrator"
        )
        db.add(audit_log)
        
        await db.commit()
        
        return {
            "success": True,
            "message": f"User {user.email} has been {action.lower()}",
            "is_locked": user.is_locked
        }
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Error toggling user lock: {str(e)}")


@router.post("/users/{user_id}/deactivate")
async def deactivate_user(user_id: UUID, db: AsyncSession = Depends(get_session)):
    """Deactivate an active user account"""
    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        user.is_active = False
        audit_log = AuditLog(
            id=uuid.uuid4(),
            user_id=user.id,
            action="USER_DEACTIVATED",
            module="auth",
            details=f"User {user.email} deactivated by administrator"
        )
        db.add(audit_log)
        await db.commit()
        return {"success": True, "message": f"User {user.email} has been deactivated"}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Error deactivating user: {str(e)}")


class UpdateRoleRequest(BaseModel):
    role: str

@router.put("/users/{user_id}/role")
async def update_user_role(user_id: UUID, body: UpdateRoleRequest, db: AsyncSession = Depends(get_session)):
    """Update a user's role"""
    valid_roles = ["admin", "sales_staff", "marketer", "customer_care", "production_staff", "warehouse_logistics"]
    if body.role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {', '.join(valid_roles)}")
    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        old_role = user.role
        user.role = body.role
        audit_log = AuditLog(
            id=uuid.uuid4(),
            user_id=user.id,
            action="USER_ROLE_CHANGED",
            module="auth",
            details=f"User {user.email} role changed from {old_role} to {body.role}"
        )
        db.add(audit_log)
        await db.commit()
        return {"success": True, "message": f"User {user.email} role changed to {body.role}"}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating role: {str(e)}")


@router.post("/users/{user_id}/reset-password")
async def admin_reset_password(user_id: UUID, db: AsyncSession = Depends(get_session)):
    """Reset user password to a temporary default (user must change on next login)"""
    try:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        temp_password = "Welcome@123"
        user.hashed_password = hash_password(temp_password)
        user.failed_login_attempts = 0
        user.is_locked = False
        audit_log = AuditLog(
            id=uuid.uuid4(),
            user_id=user.id,
            action="PASSWORD_RESET_BY_ADMIN",
            module="auth",
            details=f"Password reset for {user.email} by administrator"
        )
        db.add(audit_log)
        await db.commit()
        return {"success": True, "message": f"Password for {user.email} reset to temporary password: {temp_password}"}
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Error resetting password: {str(e)}")


# ======================== MODULE ACCESS MANAGEMENT ========================

ALL_MODULES = [
    'dashboard', 'staff', 'attendance', 'products', 'rawMaterials',
    'stockManagement', 'production', 'productionCompletions', 'consumables',
    'machinesEquipment', 'transfers', 'sales', 'paymentTracking', 'procurement',
    'logistics', 'marketing', 'hrCustomerCare', 'reports', 'financial',
    'userManagement', 'settings'
]

MODULE_LABELS = {
    'dashboard': 'Dashboard', 'staff': 'Staff', 'attendance': 'Attendance',
    'products': 'Products', 'rawMaterials': 'Raw Materials',
    'stockManagement': 'Stock Management', 'production': 'Production',
    'productionCompletions': 'Prod. Completions', 'consumables': 'Consumables',
    'machinesEquipment': 'Machines & Equipment', 'transfers': 'Transfers',
    'sales': 'Sales', 'paymentTracking': 'Payments & Debt',
    'procurement': 'Procurement', 'logistics': 'Logistics',
    'marketing': 'Marketer', 'hrCustomerCare': 'HR / Customer Care',
    'reports': 'Reports', 'financial': 'Financial',
    'userManagement': 'User Management', 'settings': 'Settings'
}


class ModuleAccessUpdate(BaseModel):
    modules: dict  # { "staff": true, "sales": false, ... }


@router.get("/modules/list")
async def list_all_modules():
    """Return the list of all application modules"""
    return [{"key": k, "label": MODULE_LABELS.get(k, k)} for k in ALL_MODULES]


@router.get("/modules/access/{user_id}")
async def get_user_module_access(user_id: UUID, db: AsyncSession = Depends(get_session)):
    """Get module access for a specific user"""
    result = await db.execute(
        select(UserModuleAccess).where(UserModuleAccess.user_id == user_id)
    )
    rows = result.scalars().all()
    access_map = {r.module_key: r.is_granted for r in rows}
    # If no records exist yet, admin gets all, others get dashboard only
    user_result = await db.execute(select(User).where(User.id == user_id))
    user = user_result.scalar_one_or_none()
    is_admin = user and user.role == 'admin'
    modules = {}
    for m in ALL_MODULES:
        if m in access_map:
            modules[m] = access_map[m]
        else:
            modules[m] = True if is_admin else (m == 'dashboard')
    return {"user_id": str(user_id), "modules": modules}


@router.get("/modules/access-all")
async def get_all_users_module_access(db: AsyncSession = Depends(get_session)):
    """Get module access for all users (admin view)"""
    users_result = await db.execute(select(User).order_by(User.full_name))
    users = users_result.scalars().all()
    access_result = await db.execute(select(UserModuleAccess))
    all_access = access_result.scalars().all()
    access_by_user = {}
    for a in all_access:
        uid = str(a.user_id)
        if uid not in access_by_user:
            access_by_user[uid] = {}
        access_by_user[uid][a.module_key] = a.is_granted
    result = []
    for u in users:
        uid = str(u.id)
        is_admin = u.role == 'admin'
        user_access = access_by_user.get(uid, {})
        modules = {}
        for m in ALL_MODULES:
            if m in user_access:
                modules[m] = user_access[m]
            else:
                modules[m] = True if is_admin else (m == 'dashboard')
        result.append({
            "user_id": uid,
            "full_name": u.full_name,
            "email": u.email,
            "role": u.role,
            "is_active": u.is_active,
            "modules": modules
        })
    return result


@router.put("/modules/access/{user_id}")
async def set_user_module_access(user_id: UUID, body: ModuleAccessUpdate, db: AsyncSession = Depends(get_session)):
    """Set module access for a specific user"""
    try:
        for module_key, is_granted in body.modules.items():
            if module_key not in ALL_MODULES:
                continue
            result = await db.execute(
                select(UserModuleAccess).where(
                    UserModuleAccess.user_id == user_id,
                    UserModuleAccess.module_key == module_key
                )
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.is_granted = is_granted
            else:
                db.add(UserModuleAccess(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    module_key=module_key,
                    is_granted=is_granted
                ))
        audit_log = AuditLog(
            id=uuid.uuid4(),
            user_id=user_id,
            action="MODULE_ACCESS_UPDATED",
            module="auth",
            details=f"Module access updated for user {user_id}"
        )
        db.add(audit_log)
        await db.commit()
        return {"success": True, "message": "Module access updated successfully"}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Error updating module access: {str(e)}")

