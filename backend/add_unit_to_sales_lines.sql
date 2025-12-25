-- Quick fix: Add unit column to sales_order_lines table
ALTER TABLE sales_order_lines ADD COLUMN IF NOT EXISTS unit VARCHAR(50);

-- Set default unit to 'each' for existing records
UPDATE sales_order_lines SET unit = 'each' WHERE unit IS NULL;

-- Verify the change
SELECT column_name, data_type, character_maximum_length 
FROM information_schema.columns 
WHERE table_name = 'sales_order_lines' 
  AND column_name = 'unit';
