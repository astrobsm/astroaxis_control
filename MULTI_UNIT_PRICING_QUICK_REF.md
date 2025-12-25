# Multi-Unit Pricing - Quick Reference

## 🚀 Quick Start

**Production URL:** http://209.38.226.32  
**Feature:** Multiple Units of Measure with Retail & Wholesale Pricing  
**Status:** ✅ DEPLOYED (May 31, 2025)

---

## 📝 How to Add a Product with Multiple Units

### Step-by-Step Guide

1. **Open Product Form**
   - Login to ASTRO-ASIX ERP
   - Go to "Product Management"
   - Click "Add Product" button

2. **Fill Basic Information**
   ```
   Product Code/SKU: MED-001
   Product Name: Medical Gauze Roll
   Manufacturer: ABC Medical Supplies
   Cost Price: ₦350 (base manufacturing cost)
   ```

3. **Add Units of Measure & Pricing**
   
   **First Unit (Automatic):**
   - Unit: `Roll`
   - Retail Price: ₦500
   - Wholesale Price: ₦400
   
   **Add Second Unit:**
   - Click "Add Unit" button
   - Unit: `Box of 12`
   - Retail Price: ₦5,500
   - Wholesale Price: ₦4,500
   
   **Add Third Unit:**
   - Click "Add Unit" button
   - Unit: `Carton of 144`
   - Retail Price: ₦60,000
   - Wholesale Price: ₦48,000

4. **Submit Form**
   - Click "Add Product" button
   - Product saved with all pricing tiers

---

## 🎯 Business Scenarios

### Scenario 1: Medical Supplies
```
Product: Surgical Gloves

Unit          | Retail Price | Wholesale Price | Target Customer
------------- | ------------ | --------------- | ---------------
Pair          | ₦200        | ₦180           | Walk-in customers
Box (50 pairs)| ₦8,500      | ₦7,500         | Small clinics
Carton (500)  | ₦75,000     | ₦65,000        | Large hospitals
```

### Scenario 2: Pharmaceuticals
```
Product: Paracetamol Tablets

Unit              | Retail Price | Wholesale Price | Use Case
----------------- | ------------ | --------------- | ---------
Blister (10 tabs) | ₦150        | ₦120           | OTC sales
Box (10 blisters) | ₦1,400      | ₦1,100         | Clinic supplies
Wholesale Pack    | ₦12,000     | ₦9,500         | Hospital procurement
```

### Scenario 3: Raw Materials
```
Product: Medical Cotton

Unit        | Retail Price | Wholesale Price | Customer Type
----------- | ------------ | --------------- | -------------
100g Pack   | ₦500        | ₦450           | Small businesses
1kg Bag     | ₦4,500      | ₦4,000         | Medium buyers
25kg Bulk   | ₦100,000    | ₦90,000        | Large manufacturers
```

---

## 🔧 UI Controls

### Add Unit Button
- **Location:** Bottom of pricing section
- **Action:** Adds new row for unit/pricing entry
- **Validation:** All fields required (unit name, retail price, wholesale price)

### Remove Button
- **Location:** Right side of each pricing row
- **Action:** Removes specific pricing row
- **Protection:** Disabled when only one row remains

### Form Fields
- **Unit:** Text input (max 50 characters)
  - Examples: "piece", "box", "carton", "roll", "pack"
- **Retail Price:** Number input, 2 decimal places, Nigerian Naira (₦)
- **Wholesale Price:** Number input, 2 decimal places, Nigerian Naira (₦)

---

## 🧪 Quick Test

### Test Product Creation

**Sample Data:**
```
SKU: TEST-001
Name: Test Medical Product
Manufacturer: Test Supplier
Cost Price: 100

Unit Pricing:
1. Unit: "Piece" | Retail: 200 | Wholesale: 150
2. Unit: "Box of 10" | Retail: 1800 | Wholesale: 1400
3. Unit: "Carton of 100" | Retail: 16000 | Wholesale: 13000
```

**Expected Result:**
- Product saved successfully
- Three pricing tiers visible in API response
- Each unit has separate retail and wholesale pricing

---

## 🔍 API Quick Reference

### Get Products with Pricing
```bash
curl http://209.38.226.32/api/products/ | jq
```

**Response Structure:**
```json
{
  "items": [
    {
      "id": "uuid",
      "sku": "MED-001",
      "name": "Medical Gauze Roll",
      "pricing": [
        {
          "id": "uuid",
          "unit": "Roll",
          "retail_price": "500.00",
          "wholesale_price": "400.00"
        }
      ]
    }
  ]
}
```

### Create Product with Pricing
```bash
curl -X POST http://209.38.226.32/api/products/ \
  -H "Content-Type: application/json" \
  -d '{
    "sku": "MED-001",
    "name": "Medical Gauze Roll",
    "cost_price": 350,
    "pricing": [
      {"unit": "Roll", "retail_price": 500, "wholesale_price": 400},
      {"unit": "Box of 12", "retail_price": 5500, "wholesale_price": 4500}
    ]
  }'
```

---

## ⚡ Tips & Best Practices

### Pricing Strategy
1. **Base Unit:** Start with smallest sellable unit (piece, roll, etc.)
2. **Bulk Discount:** Decrease unit price as quantity increases
3. **Wholesale Margin:** Wholesale price typically 15-25% below retail
4. **Cost Coverage:** Ensure all pricing covers base cost_price

### Unit Naming
- ✅ **Good:** "Roll", "Box of 12", "Carton of 144"
- ✅ **Good:** "Blister (10 tabs)", "Pack", "Wholesale Unit"
- ❌ **Avoid:** Inconsistent naming, unclear quantities

### Validation Rules
- Minimum 1 pricing entry required
- All fields required (unit, retail_price, wholesale_price)
- Prices must be >= 0
- Unit name max 50 characters

---

## 🔄 Cache Management

### Clear Browser Cache
If you don't see the new form:

**Windows:**
- Chrome/Edge: `Ctrl + Shift + R`
- Firefox: `Ctrl + F5`

**Mac:**
- Chrome/Edge: `Cmd + Shift + R`
- Firefox: `Cmd + Shift + R`

**Alternative:**
- Chrome/Edge: Settings → Privacy → Clear browsing data → Cached images and files
- Firefox: Settings → Privacy → Clear Data → Cached Web Content

---

## 📊 Database Schema

```sql
-- Product Pricing Table
CREATE TABLE product_pricing (
    id UUID PRIMARY KEY,
    product_id UUID REFERENCES products(id) ON DELETE CASCADE,
    unit VARCHAR(50) NOT NULL,
    retail_price NUMERIC(18,2) NOT NULL,
    wholesale_price NUMERIC(18,2) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now()
);

-- Index for fast lookups
CREATE INDEX ix_product_pricing_product_id ON product_pricing(product_id);
```

---

## 🐛 Troubleshooting

### Problem: Pricing not saving
**Solution:** Check all required fields are filled (unit, retail_price, wholesale_price)

### Problem: Can't remove pricing row
**Solution:** At least one pricing row must remain (by design)

### Problem: Old form still showing
**Solution:** Hard refresh browser (Ctrl+Shift+R) to clear cache

### Problem: Backend error on save
**Solution:** Check backend logs: `ssh root@209.38.226.32 "docker logs astroaxis_backend"`

---

## 📞 Support

**Documentation:** `MULTI_UNIT_PRICING_DEPLOYMENT.md` (full technical details)  
**Production URL:** http://209.38.226.32  
**Deployment Date:** May 31, 2025  
**Version:** v2.2

**Need Help?**
- Check backend logs for API errors
- Verify database migration applied
- Clear browser cache and retry
- Review test cases in deployment documentation

---

## ✅ Feature Status

**Backend:** ✅ Deployed  
**Frontend:** ✅ Deployed  
**Database:** ✅ Migrated  
**Testing:** ✅ Ready  

**Ready to use!** Clear your browser cache and start adding products with multiple units.
