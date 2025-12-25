"""
ASTRO-ASIX ERP - Push Notification API
Handles Web Push subscriptions and sending push notifications
"""
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from app.db import get_session
from app.models import User
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from pathlib import Path
import uuid
import json
import os
from datetime import datetime
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

# VAPID keys for Web Push - In production, store these securely in environment variables
# Generated using: vapid --gen && vapid --applicationServerKey
# Private key is stored in private_key.pem, public key from --applicationServerKey
VAPID_PRIVATE_KEY = os.getenv('VAPID_PRIVATE_KEY', 'private_key.pem')  # Path to PEM file or raw key
VAPID_PUBLIC_KEY = os.getenv('VAPID_PUBLIC_KEY', 'BCNDdkFgeDx5Z4pds1SBkokEehIKOMXmfw4bn9804AqUM3brbDWac3gaciTfULA2eOKKfKyhPujzC1toVrlQQzY')
VAPID_CLAIMS = {
    "sub": "mailto:astrobsm@gmail.com"
}

# Pydantic schemas
class PushSubscription(BaseModel):
    endpoint: str
    keys: Dict[str, str]
    expirationTime: Optional[int] = None

class SubscribeRequest(BaseModel):
    subscription: Dict[str, Any]

class UnsubscribeRequest(BaseModel):
    endpoint: str

class SendNotificationRequest(BaseModel):
    user_id: Optional[str] = None
    title: str
    body: str
    icon: Optional[str] = "/logo192.png"
    badge: Optional[str] = "/favicon.ico"
    url: Optional[str] = "/"
    tag: Optional[str] = None
    data: Optional[Dict[str, Any]] = None

class NotificationType:
    LOW_STOCK = "low_stock"
    ORDER_UPDATE = "order_update"
    BIRTHDAY = "birthday"
    PRODUCTION = "production"
    PAYMENT = "payment"
    SYSTEM = "system"

# In-memory subscription store (in production, use database)
# This will be migrated to database model
push_subscriptions: Dict[str, Dict[str, Any]] = {}


@router.post("/subscribe")
async def subscribe_to_push(
    request: SubscribeRequest,
    session: AsyncSession = Depends(get_session)
):
    """Register a push notification subscription for the current user"""
    try:
        subscription = request.subscription
        endpoint = subscription.get('endpoint')
        
        if not endpoint:
            raise HTTPException(status_code=400, detail="Invalid subscription: missing endpoint")
        
        # Store subscription (keyed by endpoint for deduplication)
        push_subscriptions[endpoint] = {
            "subscription": subscription,
            "subscribed_at": datetime.utcnow().isoformat(),
            "active": True
        }
        
        # TODO: In production, save to database with user_id association
        # await save_subscription_to_db(session, user_id, subscription)
        
        return {
            "success": True,
            "message": "Successfully subscribed to push notifications",
            "subscription_count": len(push_subscriptions)
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to subscribe: {str(e)}")


@router.post("/unsubscribe")
async def unsubscribe_from_push(
    request: UnsubscribeRequest,
    session: AsyncSession = Depends(get_session)
):
    """Remove a push notification subscription"""
    try:
        endpoint = request.endpoint
        
        if endpoint in push_subscriptions:
            del push_subscriptions[endpoint]
            
        return {
            "success": True,
            "message": "Successfully unsubscribed from push notifications"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to unsubscribe: {str(e)}")


@router.get("/status")
async def get_notification_status():
    """Get push notification service status"""
    return {
        "enabled": True,
        "vapid_public_key": VAPID_PUBLIC_KEY,
        "active_subscriptions": len(push_subscriptions),
        "supported_types": [
            NotificationType.LOW_STOCK,
            NotificationType.ORDER_UPDATE,
            NotificationType.BIRTHDAY,
            NotificationType.PRODUCTION,
            NotificationType.PAYMENT,
            NotificationType.SYSTEM
        ]
    }


@router.post("/send")
async def send_push_notification(
    request: SendNotificationRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session)
):
    """Send a push notification to subscribed users"""
    try:
        notification_payload = {
            "title": request.title,
            "body": request.body,
            "icon": request.icon,
            "badge": request.badge,
            "url": request.url,
            "tag": request.tag or f"astroaxis-{datetime.utcnow().timestamp()}",
            "data": request.data or {},
            "timestamp": datetime.utcnow().isoformat()
        }
        
        # Queue notification sending in background
        background_tasks.add_task(
            broadcast_push_notification,
            notification_payload
        )
        
        return {
            "success": True,
            "message": f"Notification queued for {len(push_subscriptions)} subscribers",
            "notification_id": notification_payload["tag"]
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send notification: {str(e)}")


@router.post("/test")
async def send_test_notification(
    background_tasks: BackgroundTasks
):
    """Send a test notification to all subscribers"""
    notification_payload = {
        "title": "ASTRO-ASIX ERP",
        "body": "Push notifications are working! You'll receive important updates here.",
        "icon": "/logo192.png",
        "badge": "/favicon.ico",
        "url": "/",
        "tag": "test-notification",
        "data": {"type": "test"},
        "timestamp": datetime.utcnow().isoformat()
    }
    
    background_tasks.add_task(
        broadcast_push_notification,
        notification_payload
    )
    
    return {
        "success": True,
        "message": "Test notification sent",
        "subscribers": len(push_subscriptions)
    }


async def broadcast_push_notification(payload: dict):
    """Send push notification to all active subscriptions"""
    try:
        # Try to import pywebpush (optional dependency)
        try:
            from pywebpush import webpush, WebPushException
            
            failed_endpoints = []
            
            # Resolve private key path
            private_key = VAPID_PRIVATE_KEY
            if private_key.endswith('.pem'):
                # It's a file path - resolve relative to app directory
                key_path = Path(__file__).parent.parent.parent / private_key
                if key_path.exists():
                    private_key = str(key_path)
                else:
                    print(f"Warning: VAPID private key file not found at {key_path}")
                    return
            
            for endpoint, sub_data in list(push_subscriptions.items()):
                try:
                    subscription = sub_data["subscription"]
                    webpush(
                        subscription_info=subscription,
                        data=json.dumps(payload),
                        vapid_private_key=private_key,
                        vapid_claims=VAPID_CLAIMS
                    )
                except WebPushException as e:
                    # Remove invalid subscriptions (410 Gone, 404 Not Found)
                    if e.response and e.response.status_code in [404, 410]:
                        failed_endpoints.append(endpoint)
                    print(f"Push failed for {endpoint}: {e}")
            
            # Clean up failed subscriptions
            for endpoint in failed_endpoints:
                push_subscriptions.pop(endpoint, None)
                
        except ImportError:
            # pywebpush not installed - log the notification
            print(f"[PUSH NOTIFICATION] pywebpush not installed. Would send: {payload}")
            print("Install with: pip install pywebpush")
            
    except Exception as e:
        print(f"Error broadcasting push notification: {e}")


# Notification trigger functions (called from other parts of the app)
async def notify_low_stock(product_name: str, current_stock: float, reorder_level: float):
    """Send low stock alert notification"""
    payload = {
        "title": "Low Stock Alert",
        "body": f"{product_name} is running low ({current_stock} remaining, reorder at {reorder_level})",
        "icon": "/logo192.png",
        "url": "/stock",
        "tag": f"low-stock-{product_name}",
        "data": {"type": NotificationType.LOW_STOCK, "product": product_name}
    }
    await broadcast_push_notification(payload)


async def notify_order_update(order_number: str, status: str, customer_name: str):
    """Send order status update notification"""
    payload = {
        "title": "Order Update",
        "body": f"Order {order_number} for {customer_name} is now {status}",
        "icon": "/logo192.png",
        "url": f"/sales?order={order_number}",
        "tag": f"order-{order_number}",
        "data": {"type": NotificationType.ORDER_UPDATE, "order": order_number}
    }
    await broadcast_push_notification(payload)


async def notify_birthday(staff_name: str, date: str):
    """Send birthday reminder notification"""
    payload = {
        "title": "Birthday Reminder",
        "body": f"Today is {staff_name}'s birthday! Don't forget to wish them!",
        "icon": "/logo192.png",
        "url": "/staff",
        "tag": f"birthday-{staff_name}",
        "data": {"type": NotificationType.BIRTHDAY, "staff": staff_name}
    }
    await broadcast_push_notification(payload)


async def notify_production_complete(product_name: str, quantity: int, order_id: str):
    """Send production completion notification"""
    payload = {
        "title": "Production Complete",
        "body": f"Production of {quantity} x {product_name} has been completed",
        "icon": "/logo192.png",
        "url": f"/production?order={order_id}",
        "tag": f"production-{order_id}",
        "data": {"type": NotificationType.PRODUCTION, "product": product_name}
    }
    await broadcast_push_notification(payload)


async def notify_payment_received(order_number: str, amount: float, customer_name: str):
    """Send payment received notification"""
    payload = {
        "title": "Payment Received",
        "body": f"Payment of ₦{amount:,.2f} received from {customer_name} for order {order_number}",
        "icon": "/logo192.png",
        "url": f"/sales?order={order_number}",
        "tag": f"payment-{order_number}",
        "data": {"type": NotificationType.PAYMENT, "order": order_number}
    }
    await broadcast_push_notification(payload)
