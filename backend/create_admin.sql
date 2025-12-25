-- Create admin user with phone 08033328385 and password NATISS
-- Password hash: SHA256('NATISS') = fac8947bc57acf27aa19f0abdc9568ca8e9a1b1e3c99ca8f5d4d8570cfe41faa
INSERT INTO users (id, email, hashed_password, full_name, phone, role, is_active, created_at, updated_at)
VALUES (
    gen_random_uuid(),
    'admin@astroasix.com',
    'fac8947bc57acf27aa19f0abdc9568ca8e9a1b1e3c99ca8f5d4d8570cfe41faa',
    'Administrator',
    '08033328385',
    'admin',
    true,
    NOW(),
    NOW()
)
ON CONFLICT (email) DO UPDATE SET
    hashed_password = EXCLUDED.hashed_password,
    phone = EXCLUDED.phone,
    role = EXCLUDED.role,
    full_name = EXCLUDED.full_name,
    is_active = true,
    updated_at = NOW();

-- Show the created user
SELECT id, email, full_name, phone, role, is_active, created_at
FROM users
WHERE email = 'admin@astroasix.com';
