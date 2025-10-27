"""Test if the Permission models load correctly"""
import sys
sys.path.insert(0, '.')

try:
    from app.models import User, Permission, UserPermission
    print("✅ Successfully imported User, Permission, UserPermission models")
    print(f"User table: {User.__tablename__}")
    print(f"Permission table: {Permission.__tablename__}")
    print(f"UserPermission table: {UserPermission.__tablename__}")
    
    # Try to access relationships
    print(f"\nUserPermission relationships:")
    print(f"  - user: {UserPermission.user}")
    print(f"  - permission: {UserPermission.permission}")
    print(f"  - granter: {UserPermission.granter}")
    
    print("\n✅ All models loaded successfully!")
except Exception as e:
    print(f"❌ Error loading models: {e}")
    import traceback
    traceback.print_exc()
