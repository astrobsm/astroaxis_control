-- Check admin user status
SELECT email, full_name, role, is_active, is_locked, failed_login_attempts 
FROM users 
WHERE email = 'admin@astroasix.com';

-- Reset the account if locked
UPDATE users 
SET is_locked = false, 
    is_active = true, 
    failed_login_attempts = 0
WHERE email = 'admin@astroasix.com';

-- Show updated status
SELECT email, full_name, role, is_active, is_locked, failed_login_attempts 
FROM users 
WHERE email = 'admin@astroasix.com';
