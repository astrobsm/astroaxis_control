#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sales Order Form Enhancement - Line-based Replacement"""

import shutil
from datetime import datetime
from pathlib import Path

# File paths
APP_MAIN_PATH = Path("C:/Users/USER/ASTROAXIS/frontend/src/AppMain.js")
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
BACKUP_PATH = APP_MAIN_PATH.parent / f"AppMain.js.backup_{timestamp}"

print("🎯 Enhancing Sales Order Form (Line-Based Approach)...")

# Create backup
print(f"📦 Creating backup: {BACKUP_PATH}")
shutil.copy2(APP_MAIN_PATH, BACKUP_PATH)

# Read all lines
with open(APP_MAIN_PATH, 'r', encoding='utf-8') as f:
    lines = f.readlines()

print(f"📝 File has {len(lines)} lines")
print(f"📍 Replacing lines 2093-2118 (Sales Order form)...")

# New enhanced sales order form (lines 2093-2118 will be replaced)
new_sales_form_lines = [
    "              {showForm === 'salesOrder' && (\n",
    "                <form onSubmit={submitSalesOrder}>\n",
    "                  {/* Order Type Selection */}\n",
    "                  <div className=\"form-group\" style={{marginBottom:'20px', padding:'15px', background:'#f8f9fa', borderRadius:'8px'}}>\n",
    "                    <label style={{fontWeight:'600', marginBottom:'10px', display:'block'}}>Order Type *</label>\n",
    "                    <div style={{display:'flex', gap:'20px'}}>\n",
    "                      <label style={{cursor:'pointer', padding:'10px 20px', border:'2px solid #667eea', borderRadius:'6px', background: forms.salesOrder.order_type === 'retail' ? '#667eea' : 'white', color: forms.salesOrder.order_type === 'retail' ? 'white' : '#667eea', fontWeight:'600', transition:'all 0.3s'}}>\n",
    "                        <input type=\"radio\" name=\"order_type\" value=\"retail\" checked={forms.salesOrder.order_type === 'retail'} onChange={(e)=>handleFormChange('salesOrder','order_type',e.target.value)} style={{marginRight:'8px'}}/>\n",
    "                        🛍️ Retail Order\n",
    "                      </label>\n",
    "                      <label style={{cursor:'pointer', padding:'10px 20px', border:'2px solid #667eea', borderRadius:'6px', background: forms.salesOrder.order_type === 'wholesale' ? '#667eea' : 'white', color: forms.salesOrder.order_type === 'wholesale' ? 'white' : '#667eea', fontWeight:'600', transition:'all 0.3s'}}>\n",
    "                        <input type=\"radio\" name=\"order_type\" value=\"wholesale\" checked={forms.salesOrder.order_type === 'wholesale'} onChange={(e)=>handleFormChange('salesOrder','order_type',e.target.value)} style={{marginRight:'8px'}}/>\n",
    "                        📦 Wholesale Order\n",
    "                      </label>\n",
    "                    </div>\n",
    "                  </div>\n",
    "\n",
    "                  <div className=\"form-row\">\n",
    "                    <div className=\"form-group\"><label>Customer *</label><select value={forms.salesOrder.customer_id} onChange={(e)=>handleFormChange('salesOrder','customer_id',e.target.value)} required><option value=\"\">Select customer</option>{(data.customers||[]).map(c=>(<option key={c.id} value={c.id}>{c.name}</option>))}</select></div>\n",
    "                    <div className=\"form-group\"><button type=\"button\" className=\"btn btn-secondary\" onClick={()=>openForm('customer')} style={{marginTop:'1.5rem'}}>➕ New Customer</button></div>\n",
    "                    <div className=\"form-group\"><label>Required Date</label><input type=\"date\" value={forms.salesOrder.required_date} onChange={(e)=>handleFormChange('salesOrder','required_date',e.target.value)}/></div>\n",
    "                  </div>\n",
    "                  <div className=\"form-group\"><label>Notes</label><textarea rows=\"2\" value={forms.salesOrder.notes} onChange={(e)=>handleFormChange('salesOrder','notes',e.target.value)}/></div>\n",
    "\n",
    "                  <div className=\"lines-section\">\n",
    "                    <div className=\"lines-header\">\n",
    "                      <h4>Order Lines</h4>\n",
    "                      <button type=\"button\" className=\"btn btn-secondary\" onClick={addSalesLine}>➕ Add Line</button>\n",
    "                    </div>\n",
    "                    {(forms.salesOrder.lines||[]).map((line, idx) => {\n",
    "                      const selectedProduct = (data.products||[]).find(p => p.id === line.product_id);\n",
    "                      const lineTotal = (parseFloat(line.quantity)||0) * (parseFloat(line.unit_price)||0);\n",
    "                      \n",
    "                      return (\n",
    "                        <div key={idx} className=\"form-row line-row\" style={{position:'relative', background:'#f8f9fa', padding:'15px', borderRadius:'8px', marginBottom:'10px'}}>\n",
    "                          <div className=\"form-group\"><label>Product *</label><select value={line.product_id} onChange={(e)=>updateSalesLine(idx,'product_id',e.target.value)} required><option value=\"\">Select product</option>{(data.products||[]).map(p=>(<option key={p.id} value={p.id}>{p.name}</option>))}</select></div>\n",
    "                          \n",
    "                          <div className=\"form-group\">\n",
    "                            <label>Unit of Measure *</label>\n",
    "                            <select value={line.unit} onChange={(e)=>updateSalesLine(idx,'unit',e.target.value)} required disabled={!line.product_id}>\n",
    "                              <option value=\"\">Select unit</option>\n",
    "                              {selectedProduct && selectedProduct.pricing && selectedProduct.pricing.map((pr,i)=>(\n",
    "                                <option key={i} value={pr.unit}>\n",
    "                                  {pr.unit} - {forms.salesOrder.order_type === 'retail' ? formatCurrency(pr.retail_price) : formatCurrency(pr.wholesale_price)}\n",
    "                                </option>\n",
    "                              ))}\n",
    "                            </select>\n",
    "                          </div>\n",
    "                          \n",
    "                          <div className=\"form-group\"><label>Qty *</label><input type=\"number\" step=\"0.01\" value={line.quantity} onChange={(e)=>updateSalesLine(idx,'quantity',e.target.value)} required/></div>\n",
    "                          \n",
    "                          <div className=\"form-group\">\n",
    "                            <label>Unit Price (Auto)</label>\n",
    "                            <input type=\"number\" step=\"0.01\" value={line.unit_price} readOnly style={{background:'#e9ecef', cursor:'not-allowed'}}/>\n",
    "                          </div>\n",
    "                          \n",
    "                          <div className=\"form-group\">\n",
    "                            <label>Line Total</label>\n",
    "                            <div style={{padding:'10px', background:'#667eea', color:'white', borderRadius:'6px', fontWeight:'600', textAlign:'center'}}>\n",
    "                              {formatCurrency(lineTotal)}\n",
    "                            </div>\n",
    "                          </div>\n",
    "                          \n",
    "                          <div className=\"form-group\"><button type=\"button\" className=\"btn btn-danger\" onClick={()=>removeSalesLine(idx)} style={{marginTop:'1.5rem'}}>🗑️</button></div>\n",
    "                        </div>\n",
    "                      );\n",
    "                    })}\n",
    "                  </div>\n",
    "\n",
    "                  {/* Grand Total */}\n",
    "                  {forms.salesOrder.lines.length > 0 && (\n",
    "                    <div style={{marginTop:'20px', padding:'20px', background:'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', borderRadius:'8px', color:'white'}}>\n",
    "                      <div style={{display:'flex', justifyContent:'space-between', alignItems:'center'}}>\n",
    "                        <h3 style={{margin:0}}>Grand Total:</h3>\n",
    "                        <h2 style={{margin:0, fontSize:'32px', fontWeight:'700'}}>\n",
    "                          {formatCurrency(forms.salesOrder.lines.reduce((sum,line)=>{\n",
    "                            const lineTotal = (parseFloat(line.quantity)||0) * (parseFloat(line.unit_price)||0);\n",
    "                            return sum + lineTotal;\n",
    "                          }, 0))}\n",
    "                        </h2>\n",
    "                      </div>\n",
    "                    </div>\n",
    "                  )}\n",
    "\n",
    "                  <div className=\"modal-actions\"><button type=\"button\" className=\"btn btn-secondary\" onClick={()=>setShowForm('')}>Cancel</button><button type=\"submit\" className=\"btn btn-primary\" disabled={loading}>{loading?'Saving...':'Create Order'}</button></div>\n",
    "                </form>\n",
    "              )}\n",
    "\n"
]

# Replace lines 2092-2118 (0-indexed: 2091-2117, inclusive end: 2118)
lines[2092:2118] = new_sales_form_lines

# Write back
with open(APP_MAIN_PATH, 'w', encoding='utf-8', newline='') as f:
    f.writelines(lines)

print(f"✅ Replaced lines 2093-2118 with enhanced Sales Order form ({len(new_sales_form_lines)} new lines)")
print("")
print("🎉 ALL THREE MODULES COMPLETE!")
print("")
print("📋 Final Summary:")
print("   ✅ Settings Module → 6 comprehensive setting cards (Company, Localization, Inventory, Sales, Security, Modules)")
print("   ✅ Reports Module → 6 detailed dashboards (Payment, Sales, Inventory, Staff, Production, Customers)")
print("   ✅ Sales Order Form → Retail/Wholesale selector with visual styling")
print("   ✅ Sales Order Form → Unit dropdown showing prices per order type")
print("   ✅ Sales Order Form → Auto-calculated unit prices (read-only)")
print("   ✅ Sales Order Form → Line total per row")
print("   ✅ Sales Order Form → Grand total with gradient banner")
print("")
print("🚀 Ready to Deploy:")
print("   cd frontend && npm run build && cd ..")
print("   .\\deploy-frontend-simple.ps1")
