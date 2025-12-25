-- Check if products have pricing data
SELECT p.name, p.sku, pp.unit, pp.cost_price, pp.retail_price, pp.wholesale_price 
FROM products p 
LEFT JOIN product_pricing pp ON p.id = pp.product_id 
ORDER BY p.created_at DESC;