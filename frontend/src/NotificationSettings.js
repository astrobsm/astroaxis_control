/**
 * ASTRO-ASIX ERP - Notification Settings Component
 * Manages push notification preferences and subscription status
 */
import React, { useState, useEffect } from 'react';
import {
  isPushSupported,
  getNotificationPermission,
  subscribeToPush,
  unsubscribeFromPush,
  isSubscribedToPush,
  sendTestNotification
} from './utils/pushNotifications';

function NotificationSettings({ onClose }) {
  const [permission, setPermission] = useState('default');
  const [isSubscribed, setIsSubscribed] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');

  useEffect(() => {
    checkNotificationStatus();
  }, []);

  const checkNotificationStatus = async () => {
    setLoading(true);
    try {
      setPermission(getNotificationPermission());
      const subscribed = await isSubscribedToPush();
      setIsSubscribed(subscribed);
    } catch (err) {
      setError('Failed to check notification status');
    }
    setLoading(false);
  };

  const handleEnableNotifications = async () => {
    setLoading(true);
    setError('');
    setSuccess('');
    
    try {
      await subscribeToPush();
      setIsSubscribed(true);
      setPermission('granted');
      setSuccess('Push notifications enabled successfully!');
    } catch (err) {
      setError(err.message || 'Failed to enable notifications');
    }
    
    setLoading(false);
  };

  const handleDisableNotifications = async () => {
    setLoading(true);
    setError('');
    setSuccess('');
    
    try {
      await unsubscribeFromPush();
      setIsSubscribed(false);
      setSuccess('Push notifications disabled');
    } catch (err) {
      setError(err.message || 'Failed to disable notifications');
    }
    
    setLoading(false);
  };

  const handleTestNotification = async () => {
    setLoading(true);
    setError('');
    
    try {
      await sendTestNotification();
      setSuccess('Test notification sent! Check your notifications.');
    } catch (err) {
      setError(err.message || 'Failed to send test notification');
    }
    
    setLoading(false);
  };

  if (!isPushSupported()) {
    return (
      <div className="notification-settings">
        <h3>Push Notifications</h3>
        <div className="warning-box">
          Push notifications are not supported in this browser. 
          Try using Chrome, Firefox, or Edge.
        </div>
      </div>
    );
  }

  return (
    <div className="notification-settings">
      <div className="settings-header">
        <h3>Push Notification Settings</h3>
        {onClose && (
          <button className="close-btn" onClick={onClose}>&times;</button>
        )}
      </div>

      {error && <div className="error-message">{error}</div>}
      {success && <div className="success-message">{success}</div>}

      <div className="setting-item">
        <label>Notification Permission</label>
        <div className={`status-badge ${permission}`}>
          {permission === 'granted' ? 'Allowed' : 
           permission === 'denied' ? 'Blocked' : 'Not Set'}
        </div>
      </div>

      <div className="setting-item">
        <label>Push Subscription</label>
        <div className={`status-badge ${isSubscribed ? 'active' : 'inactive'}`}>
          {isSubscribed ? 'Active' : 'Not Subscribed'}
        </div>
      </div>

      {permission === 'denied' && (
        <div className="warning-box">
          Notifications are blocked. Please enable them in your browser settings:
          <br />
          <small>Chrome: Click the lock icon in address bar → Site Settings → Notifications</small>
        </div>
      )}

      <div className="setting-actions">
        {!isSubscribed ? (
          <button 
            className="btn btn-primary"
            onClick={handleEnableNotifications}
            disabled={loading || permission === 'denied'}
          >
            {loading ? 'Enabling...' : 'Enable Push Notifications'}
          </button>
        ) : (
          <>
            <button 
              className="btn btn-secondary"
              onClick={handleTestNotification}
              disabled={loading}
            >
              {loading ? 'Sending...' : 'Send Test Notification'}
            </button>
            <button 
              className="btn btn-danger"
              onClick={handleDisableNotifications}
              disabled={loading}
            >
              {loading ? 'Disabling...' : 'Disable Notifications'}
            </button>
          </>
        )}
      </div>

      <div className="notification-types">
        <h4>You'll receive notifications for:</h4>
        <ul>
          <li><strong>Low Stock Alerts</strong> - When products fall below reorder level</li>
          <li><strong>Order Updates</strong> - Status changes on sales orders</li>
          <li><strong>Birthday Reminders</strong> - Staff birthdays</li>
          <li><strong>Production Updates</strong> - Production order completions</li>
          <li><strong>Payment Notifications</strong> - Payment received alerts</li>
        </ul>
      </div>

      <style jsx>{`
        .notification-settings {
          padding: 20px;
        }
        .settings-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 20px;
        }
        .settings-header h3 {
          margin: 0;
        }
        .close-btn {
          background: none;
          border: none;
          font-size: 24px;
          cursor: pointer;
          color: #666;
        }
        .setting-item {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 12px 0;
          border-bottom: 1px solid #eee;
        }
        .status-badge {
          padding: 4px 12px;
          border-radius: 20px;
          font-size: 12px;
          font-weight: 600;
        }
        .status-badge.granted, .status-badge.active {
          background: #d4edda;
          color: #155724;
        }
        .status-badge.denied {
          background: #f8d7da;
          color: #721c24;
        }
        .status-badge.default, .status-badge.inactive {
          background: #fff3cd;
          color: #856404;
        }
        .warning-box {
          background: #fff3cd;
          border: 1px solid #ffc107;
          border-radius: 8px;
          padding: 12px;
          margin: 16px 0;
          color: #856404;
        }
        .error-message {
          background: #f8d7da;
          border: 1px solid #f5c6cb;
          border-radius: 8px;
          padding: 12px;
          margin-bottom: 16px;
          color: #721c24;
        }
        .success-message {
          background: #d4edda;
          border: 1px solid #c3e6cb;
          border-radius: 8px;
          padding: 12px;
          margin-bottom: 16px;
          color: #155724;
        }
        .setting-actions {
          display: flex;
          gap: 12px;
          margin-top: 20px;
        }
        .btn {
          padding: 10px 20px;
          border: none;
          border-radius: 8px;
          cursor: pointer;
          font-weight: 600;
          transition: opacity 0.2s;
        }
        .btn:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }
        .btn-primary {
          background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
          color: white;
        }
        .btn-secondary {
          background: #6c757d;
          color: white;
        }
        .btn-danger {
          background: #dc3545;
          color: white;
        }
        .notification-types {
          margin-top: 24px;
          padding-top: 16px;
          border-top: 1px solid #eee;
        }
        .notification-types h4 {
          margin-bottom: 12px;
        }
        .notification-types ul {
          list-style: none;
          padding: 0;
        }
        .notification-types li {
          padding: 8px 0;
          color: #666;
        }
        .notification-types li strong {
          color: #333;
        }
      `}</style>
    </div>
  );
}

export default NotificationSettings;
