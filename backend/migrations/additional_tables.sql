-- Additional tables for Sales, Production, and Staff modules
-- Run this after the main ddl.sql
-- Connect to axis_db database first: psql -U postgres -d axis_db

-- Sales Tables
CREATE TABLE IF NOT EXISTS customers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    phone VARCHAR(32),
    address TEXT,
    credit_limit NUMERIC(18,2) DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE,
    created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sales_orders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    customer_id UUID NOT NULL REFERENCES customers(id),
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    order_date timestamptz DEFAULT now(),
    required_date timestamptz,
    total_amount NUMERIC(18,2) DEFAULT 0,
    notes TEXT,
    created_by UUID REFERENCES users(id),
    created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sales_order_lines (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    sales_order_id UUID NOT NULL REFERENCES sales_orders(id) ON DELETE CASCADE,
    product_id UUID NOT NULL REFERENCES products(id),
    quantity NUMERIC(18,6) NOT NULL,
    unit_price NUMERIC(18,6) NOT NULL,
    line_total NUMERIC(18,2) NOT NULL
);

-- Production Tables
CREATE TABLE IF NOT EXISTS production_orders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_number VARCHAR(64) UNIQUE NOT NULL,
    product_id UUID NOT NULL REFERENCES products(id),
    sales_order_id UUID REFERENCES sales_orders(id),
    quantity_requested NUMERIC(18,6) NOT NULL,
    quantity_produced NUMERIC(18,6) DEFAULT 0,
    status VARCHAR(32) NOT NULL DEFAULT 'planned',
    start_date timestamptz,
    completion_date timestamptz,
    assigned_to UUID REFERENCES users(id),
    notes TEXT,
    created_by UUID REFERENCES users(id),
    created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS production_order_materials (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    production_order_id UUID NOT NULL REFERENCES production_orders(id) ON DELETE CASCADE,
    raw_material_id UUID NOT NULL REFERENCES raw_materials(id),
    quantity_required NUMERIC(18,6) NOT NULL,
    quantity_consumed NUMERIC(18,6) DEFAULT 0,
    warehouse_id UUID REFERENCES warehouses(id)
);

-- Staff Tables
CREATE TABLE IF NOT EXISTS departments (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    description TEXT,
    manager_id UUID REFERENCES users(id),
    is_active BOOLEAN DEFAULT TRUE,
    created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS employees (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_number VARCHAR(32) UNIQUE NOT NULL,
    user_id UUID REFERENCES users(id),
    first_name VARCHAR(128) NOT NULL,
    last_name VARCHAR(128) NOT NULL,
    position VARCHAR(128),
    department_id UUID REFERENCES departments(id),
    salary NUMERIC(18,2),
    hire_date DATE,
    is_active BOOLEAN DEFAULT TRUE,
    phone VARCHAR(32),
    address TEXT,
    created_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS work_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    employee_id UUID NOT NULL REFERENCES employees(id),
    production_order_id UUID REFERENCES production_orders(id),
    work_date DATE NOT NULL,
    hours_worked NUMERIC(5,2) NOT NULL,
    description TEXT,
    created_at timestamptz DEFAULT now()
);

-- Create indexes for better performance
CREATE INDEX IF NOT EXISTS idx_customers_email ON customers(email);
CREATE INDEX IF NOT EXISTS idx_customers_active ON customers(is_active);

CREATE INDEX IF NOT EXISTS idx_sales_orders_customer_id ON sales_orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_sales_orders_status ON sales_orders(status);
CREATE INDEX IF NOT EXISTS idx_sales_orders_order_date ON sales_orders(order_date);

CREATE INDEX IF NOT EXISTS idx_sales_order_lines_order_id ON sales_order_lines(sales_order_id);
CREATE INDEX IF NOT EXISTS idx_sales_order_lines_product_id ON sales_order_lines(product_id);

CREATE INDEX IF NOT EXISTS idx_production_orders_product_id ON production_orders(product_id);
CREATE INDEX IF NOT EXISTS idx_production_orders_status ON production_orders(status);
CREATE INDEX IF NOT EXISTS idx_production_orders_assigned_to ON production_orders(assigned_to);

CREATE INDEX IF NOT EXISTS idx_production_order_materials_order_id ON production_order_materials(production_order_id);
CREATE INDEX IF NOT EXISTS idx_production_order_materials_material_id ON production_order_materials(raw_material_id);

CREATE INDEX IF NOT EXISTS idx_employees_number ON employees(employee_number);
CREATE INDEX IF NOT EXISTS idx_employees_department ON employees(department_id);
CREATE INDEX IF NOT EXISTS idx_employees_active ON employees(is_active);

CREATE INDEX IF NOT EXISTS idx_work_logs_employee_id ON work_logs(employee_id);
CREATE INDEX IF NOT EXISTS idx_work_logs_production_order_id ON work_logs(production_order_id);
CREATE INDEX IF NOT EXISTS idx_work_logs_work_date ON work_logs(work_date);

-- Insert some sample departments
INSERT INTO departments (name, description) VALUES
('Production', 'Manufacturing and production operations'),
('Sales', 'Sales and customer relations'),
('Administration', 'Administrative and management functions'),
('Quality Control', 'Quality assurance and testing')
ON CONFLICT DO NOTHING;

-- Insert some sample customers
INSERT INTO customers (name, email, phone, credit_limit) VALUES
('ABC Manufacturing Co.', 'orders@abcmfg.com', '+1-555-0101', 50000.00),
('XYZ Industries Ltd.', 'purchasing@xyzind.com', '+1-555-0102', 75000.00),
('Tech Solutions Inc.', 'procurement@techsol.com', '+1-555-0103', 100000.00)
ON CONFLICT DO NOTHING;

COMMIT;