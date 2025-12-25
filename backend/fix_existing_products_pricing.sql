-- Fix existing products by adding default pricing records
-- First, let's add pricing for each existing product

-- Get all products without pricing and add default pricing
INSERT INTO product_pricing (id, product_id, unit, cost_price, retail_price, wholesale_price, created_at)
SELECT 
    gen_random_uuid() as id,
    p.id as product_id,
    COALESCE(p.unit, 'each') as unit,
    COALESCE(p.cost_price, 0) as cost_price,
    COALESCE(p.retail_price, 0) as retail_price,
    COALESCE(p.wholesale_price, 0) as wholesale_price,
    NOW() as created_at
FROM products p
LEFT JOIN product_pricing pp ON p.id = pp.product_id
WHERE pp.id IS NULL;

-- Verify the fix
SELECT p.name, p.sku, pp.unit, pp.cost_price, pp.retail_price, pp.wholesale_price 
FROM products p 
LEFT JOIN product_pricing pp ON p.id = pp.product_id 
ORDER BY p.name;