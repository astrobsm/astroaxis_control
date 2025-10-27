# 📋 Bill of Materials (BOM) & Production Requirements Testing Guide

## ✅ System Status
- **Server**: Running on http://127.0.0.1:8004
- **Modules**: 10 modules active (staff, attendance, products, raw_materials, stock, warehouses, production, sales, stock_management, **bom**)
- **Frontend**: 59.04 kB (+1.85 kB) | CSS: 7.27 kB (+454 B)
- **Database**: PostgreSQL with BOM tables ready

---

## 🎯 BOM System Overview

The BOM (Bill of Materials) system allows you to:
1. **Register production requirements** for each product with specific raw material quantities
2. **Calculate material requirements** for any production quantity
3. **Check stock availability** across warehouses before production
4. **Automatically deduct raw materials** from inventory when production is approved
5. **Track all material movements** with audit trails via StockMovement records

---

## 📊 API Endpoints Available

### 1. POST `/api/bom/create`
**Purpose**: Create or update BOM for a product
```json
{
  "product_id": "uuid-here",
  "lines": [
    {
      "raw_material_id": "uuid-here",
      "qty_per_unit": 2.5,
      "unit": "kg"
    }
  ]
}
```

### 2. GET `/api/bom/product/{product_id}`
**Purpose**: Get BOM details for a specific product

### 3. POST `/api/bom/calculate-requirements`
**Purpose**: Calculate material needs for production quantity
```json
{
  "product_id": "uuid-here",
  "quantity_to_produce": 100,
  "warehouse_id": "uuid-here"
}
```
**Returns**: Requirements, costs, stock availability, and shortage details

### 4. POST `/api/bom/approve-production`
**Purpose**: Approve production and auto-deduct materials from stock
```json
{
  "product_id": "uuid-here",
  "quantity_to_produce": 100,
  "warehouse_id": "uuid-here"
}
```
**Actions**: Validates stock, deducts quantities, creates StockMovement records

### 5. GET `/api/bom/products-with-bom`
**Purpose**: List all products that have BOMs defined

### 6. GET `/api/bom/{bom_id}/cost`
**Purpose**: Calculate total material cost for a BOM

---

## 🧪 Testing Workflow

### **Phase 1: Setup Test Data**

#### Step 1.1: Create Raw Materials
Navigate to **Raw Materials** module → Click **➕ Add Raw Material**

Create at least 3 raw materials:
- **Sugar** | SKU: `RM-001` | Manufacturer: `Sweet Co.` | UoM: `kg` | Reorder: `50` | Cost: `₦500`
- **Flour** | SKU: `RM-002` | Manufacturer: `Grain Mills` | UoM: `kg` | Reorder: `100` | Cost: `₦300`
- **Yeast** | SKU: `RM-003` | Manufacturer: `Bio Lab` | UoM: `g` | Reorder: `10` | Cost: `₦100`

#### Step 1.2: Create Products
Navigate to **Products** module → Click **➕ Add Product**

Create a test product:
- **Name**: `Bread Loaf`
- **SKU**: `PROD-001`
- **Unit**: `pcs`
- **Description**: `Whole wheat bread loaf`

#### Step 1.3: Add Raw Material Stock
Navigate to **Stock Management** → Click **📥 Raw Material Intake**

Add stock for each raw material:
- **Sugar**: 500 kg
- **Flour**: 1000 kg
- **Yeast**: 50 g

Verify stock levels by clicking **📊 View Raw Material Stock**

---

### **Phase 2: Create BOM**

#### Step 2.1: Register BOM
1. Navigate to **Production** module
2. Click **📋 Register BOM** button (top right)
3. **BOM Form** modal opens:
   - **Select Product**: Choose `Bread Loaf`
   - **Raw Material Lines**:
     - Line 1: `Flour` | Qty: `0.5` | Unit: `kg`
     - Line 2: `Sugar` | Qty: `0.1` | Unit: `kg`
     - Line 3: `Yeast` | Qty: `10` | Unit: `g`
   - Click **➕ Add Raw Material Line** to add more lines
   - Click **🗑️** to remove unwanted lines
4. Click **💾 Save BOM**
5. ✅ Confirm success notification

#### Step 2.2: Verify BOM
1. In Production Console, select `Bread Loaf` from product dropdown
2. Click **📋 View BOM** button
3. ✅ Verify BOM displays in table format with:
   - Raw material names
   - SKU codes
   - Quantities per unit
   - Unit costs
   - Line costs
   - **Total BOM cost**

---

### **Phase 3: Calculate Requirements**

#### Step 3.1: Use Production Console
1. **Select Product**: `Bread Loaf`
2. **Quantity to Produce**: `100` (units)
3. **Warehouse**: Select your warehouse
4. Click **🧮 Calculate Requirements**

#### Step 3.2: Review Requirements Display
The system displays:

**Summary Cards**:
- **Total Material Cost**: Shows total cost for 100 units (e.g., ₦6,100)
- **Cost per Unit**: Shows cost per single unit (e.g., ₦61)
- **Production Status**: 
  - ✅ **CAN PRODUCE** (green) if sufficient stock
  - ❌ **INSUFFICIENT STOCK** (red) if shortages exist

**Requirements Table**:
| Raw Material | SKU | Qty/Unit | Required Qty | Unit | Unit Cost | Line Cost | Available | Status |
|--------------|-----|----------|--------------|------|-----------|-----------|-----------|---------|
| Flour | RM-002 | 0.5 | 50.0 | kg | ₦300 | ₦15,000 | 1000 | ✅ OK |
| Sugar | RM-001 | 0.1 | 10.0 | kg | ₦500 | ₦5,000 | 500 | ✅ OK |
| Yeast | RM-003 | 10 | 1000.0 | g | ₦100 | ₦100,000 | 50 | ❌ INSUFFICIENT |

**Shortages Section** (if any):
- Shows materials with insufficient stock
- Displays required vs. available vs. shortage amounts

---

### **Phase 4: Approve Production (Success Case)**

#### Step 4.1: Ensure Sufficient Stock
Make sure all required materials have sufficient stock in the selected warehouse.

If yeast shortage exists from Step 3.2:
1. Navigate to **Stock Management** → **📥 Raw Material Intake**
2. Add **Yeast**: 2000 g to warehouse
3. Return to **Production** module
4. Recalculate requirements

#### Step 4.2: Approve Production
1. After seeing **✅ CAN PRODUCE** status
2. Click **✅ Approve Production & Deduct Raw Materials from Stock**
3. Confirm in the approval note which warehouse will be affected
4. ✅ Success notification appears
5. Production console resets

#### Step 4.3: Verify Stock Deductions
1. Navigate to **Stock Management** → **📊 View Raw Material Stock**
2. ✅ Verify quantities deducted:
   - **Flour**: 1000 kg → 950 kg (-50 kg)
   - **Sugar**: 500 kg → 490 kg (-10 kg)
   - **Yeast**: 2000 g → 1000 g (-1000 g)

---

### **Phase 5: Test Shortage Prevention**

#### Step 5.1: Create Shortage Scenario
1. Manually reduce yeast stock to only 100 g (less than needed for 100 units)
2. Or set quantity to produce to 500 units (requires 5000 g yeast)

#### Step 5.2: Calculate with Insufficient Stock
1. **Production Console**: Select `Bread Loaf`, enter `500` units
2. Click **🧮 Calculate Requirements**

#### Step 5.3: Verify Shortage Detection
✅ **Expected Behavior**:
- **Production Status**: ❌ **INSUFFICIENT STOCK** (red background)
- **Shortages Section** appears with red warning
- **Approve button** does NOT appear
- **Requirements table** shows:
  - Yeast row highlighted in red
  - Status: ❌ INSUFFICIENT
  - Available stock clearly shown as insufficient

#### Step 5.4: Attempt Direct Approval
If you try to call `/api/bom/approve-production` directly via API with insufficient stock:
- ❌ API returns 400 error
- Error message: "Insufficient stock for [material name]"
- No stock deduction occurs
- Database remains unchanged

---

## 🔬 Advanced Testing

### Test Case 1: Multiple Products with Different BOMs
1. Create 3 different products
2. Assign unique BOMs to each
3. Verify each BOM calculates correctly
4. Approve production for different products
5. Verify independent stock deductions

### Test Case 2: BOM Editing
1. Create a BOM for a product
2. Edit the BOM (click **📋 Register BOM** again, select same product)
3. Change quantities or add/remove materials
4. Save and verify calculations update correctly

### Test Case 3: Cross-Warehouse Production
1. Create 2 warehouses
2. Split raw material stock between warehouses
3. Calculate requirements for Warehouse A
4. Approve production
5. Verify only Warehouse A stock is deducted

### Test Case 4: Decimal Precision
1. Create BOM with decimal quantities (e.g., 0.375 kg)
2. Calculate for production quantity with decimals (e.g., 75.5 units)
3. Verify calculations maintain precision
4. Confirm deductions are exact (no rounding errors)

### Test Case 5: Stock Movement Audit
1. Approve a production order
2. Navigate to **Stock Management** → **📊 Stock Analysis**
3. Verify StockMovement records created for each material
4. Check movement_type = "deduction"
5. Verify reference includes product info

---

## 🛠️ Troubleshooting

### Issue: BOM button not showing
- ✅ **Fix**: Refresh page, clear browser cache
- Verify server shows: `bom` in module list

### Issue: "Product not found" error
- ✅ **Fix**: Ensure product exists in Products module
- Check product_id is valid UUID

### Issue: Calculations incorrect
- ✅ **Fix**: Verify BOM quantities are correct
- Check raw material unit costs
- Ensure qty_per_unit uses correct units

### Issue: Stock not deducting
- ✅ **Fix**: Verify warehouse_id is correct
- Check StockLevel table has entries for materials
- Ensure production was approved (green status)

### Issue: Can't remove BOM line
- ✅ **Fix**: Must have at least 1 line
- Add new line first, then remove old one

---

## 📈 Expected Results Summary

### ✅ Successful BOM Creation
- Notification: "BOM saved successfully"
- BOM appears when clicking "View BOM"
- Cost calculations correct

### ✅ Successful Requirements Calculation
- Summary cards display correctly
- Requirements table shows all materials
- Stock availability checked accurately
- Shortages detected if any

### ✅ Successful Production Approval
- Notification: "Production approved and stock deducted"
- Raw material stock levels reduced
- StockMovement records created
- Production console resets

### ❌ Failed Approval (Insufficient Stock)
- Error notification appears
- No stock deduction occurs
- Production status shows red
- Approve button not available

---

## 🎨 UI Components Guide

### Production Console
- **Location**: Top of Production module
- **Features**: Product selector, quantity input, warehouse selector, calculate button
- **Purpose**: Primary interface for production planning

### BOM Management Buttons
- **📋 Register BOM**: Opens BOM creation/editing modal
- **📋 View BOM**: Displays BOM details in table format

### Requirements Display
- **Summary Cards**: Quick overview of costs and status
- **Requirements Table**: Detailed material breakdown
- **Shortages Section**: Red-highlighted shortage warnings
- **Approval Section**: Green section with approve button (only if sufficient stock)

### BOM Modal Form
- **Product Selector**: Choose product to assign BOM
- **Dynamic Lines**: Add/remove raw material lines
- **Line Fields**: Material, quantity, unit for each line
- **Actions**: Save or cancel

---

## 📝 Database Verification Queries

### Check BOM Records
```sql
SELECT * FROM bom WHERE product_id = '<your-product-id>';
```

### Check BOM Lines
```sql
SELECT bl.*, rm.name, rm.sku 
FROM bom_line bl 
JOIN raw_material rm ON bl.raw_material_id = rm.id 
WHERE bl.bom_id = '<your-bom-id>';
```

### Check Stock Movements
```sql
SELECT * FROM stock_movement 
WHERE movement_type = 'deduction' 
ORDER BY movement_date DESC 
LIMIT 10;
```

### Check Stock Levels
```sql
SELECT sl.*, rm.name 
FROM stock_level sl 
JOIN raw_material rm ON sl.raw_material_id = rm.id 
WHERE sl.warehouse_id = '<your-warehouse-id>';
```

---

## 🚀 Production Workflow Summary

```
1. Setup
   ├── Create Raw Materials
   ├── Create Products
   └── Add Raw Material Stock to Warehouses

2. BOM Registration
   ├── Click "Register BOM"
   ├── Select Product
   ├── Add Raw Material Lines (qty per unit)
   └── Save BOM

3. Production Planning
   ├── Select Product in Console
   ├── Enter Quantity to Produce
   ├── Select Warehouse
   └── Click "Calculate Requirements"

4. Review & Approve
   ├── Check Requirements Display
   ├── Verify Stock Availability
   ├── Review Total Costs
   └── If OK → Approve Production

5. Stock Deduction (Automatic)
   ├── System validates stock levels
   ├── Deducts materials from warehouse
   ├── Creates StockMovement records
   └── Updates StockLevel table

6. Verification
   ├── Check Stock Management
   ├── Verify quantities deducted
   └── Review stock movements
```

---

## 🎯 Success Criteria

✅ **Phase 1**: All test data created successfully  
✅ **Phase 2**: BOM saved and viewable  
✅ **Phase 3**: Requirements calculated correctly with costs  
✅ **Phase 4**: Production approved and stock deducted accurately  
✅ **Phase 5**: Shortages detected and approval prevented  
✅ **Phase 6**: Stock movements recorded for audit trail  

---

## 📞 Support

If you encounter issues:
1. Check browser console for errors (F12)
2. Verify server logs at backend terminal
3. Check database connections
4. Review API endpoint responses in Network tab
5. Verify all migrations applied: `alembic current`

**System Ready**: http://127.0.0.1:8004 ✅
