-- Quick fix: Add cost_price column to product_pricing table
ALTER TABLE product_pricing ADD COLUMN cost_price DECIMAL(10,2);

-- Update existing records with default cost_price of 0
UPDATE product_pricing SET cost_price = 0.0 WHERE cost_price IS NULL;
-- Add cost_price column if it doesn't exist

DO $$ 
BEGIN 
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='product_pricing' AND column_name='cost_price') THEN
        ALTER TABLE product_pricing ADD COLUMN cost_price DECIMAL(10,2) DEFAULT 0.00;
        UPDATE product_pricing SET cost_price = 0.00 WHERE cost_price IS NULL;
    END IF;
END $$;

-- Also ensure product_pricing table has proper indexes
CREATE INDEX IF NOT EXISTS idx_product_pricing_product_id ON product_pricing(product_id);
CREATE INDEX IF NOT EXISTS idx_product_pricing_unit ON product_pricing(unit);

-- Display current structure
SELECT column_name, data_type, is_nullable, column_default 
FROM information_schema.columns 
WHERE table_name = 'product_pricing' 
ORDER BY ordinal_position;