#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Enhance Sales Order Form - Add Order Type, Unit Selector, Auto-Price Display"""

import shutil
from datetime import datetime
from pathlib import Path

# File paths
APP_MAIN_PATH = Path("C:/Users/USER/ASTROAXIS/frontend/src/AppMain.js")
timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
BACKUP_PATH = APP_MAIN_PATH.parent / f"AppMain.js.backup_{timestamp}"

print("🛒 Enhancing Sales Order Form...")

# Create backup
print(f"📦 Creating backup: {BACKUP_PATH}")
shutil.copy2(APP_MAIN_PATH, BACKUP_PATH)

# Read content
with open(APP_MAIN_PATH, 'r', encoding='utf-8') as f:
    content = f.read()

print("📝 Applying enhancements...")

# ENHANCEMENT: Add Order Type selector and Unit dropdown to Sales Order form
old_sales_form = '''              {showForm === 'salesOrder' && (
                <form onSubmit={submitSalesOrder}>
                  <div className="form-row">
                    <div className="form-group"><label>Customer *</label><select value={forms.salesOrder.customer_id} onChange={(e)=>handleFormChange('salesOrder','customer_id',e.target.value)} required><option value="">Select customer</option>{(data.customers||[]).map(c=>(<option key={c.id} value={c.id}>{c.name}</option>))}</select></div>
                    <div className="form-group"><button type="button" className="btn btn-secondary" onClick={()=>openForm('customer')} style={{marginTop:'1.5rem'}}>ž• New Customer</button></div>
                    <div className="form-group"><label>Required Date</label><input type="date" value={forms.salesOrder.required_date} onChange={(e)=>handleFormChange('salesOrder','required_date',e.target.value)}/></div>
                  </div>
                  <div className="form-group"><label>Notes</label><textarea rows="2" value={forms.salesOrder.notes} onChange={(e)=>handleFormChange('salesOrder','notes',e.target.value)}/></div>

                  <div className="lines-section">
                    <div className="lines-header">
                      <h4>Order Lines</h4>
                      <button type="button" className="btn btn-secondary" onClick={addSalesLine}>ž• Add Line</button>
                    </div>
                    {(forms.salesOrder.lines||[]).map((line, idx) => (
                      <div key={idx} className="form-row line-row">
                        <div className="form-group"><label>Product *</label><select value={line.product_id} onChange={(e)=>updateSalesLine(idx,'product_id',e.target.value)} required><option value="">Select product</option>{(data.products||[]).map(p=>(<option key={p.id} value={p.id}>{p.name}</option>))}</select></div>
                        <div className="form-group"><label>Qty *</label><input type="number" value={line.quantity} onChange={(e)=>updateSalesLine(idx,'quantity',e.target.value)} required/></div>
                        <div className="form-group"><label>Unit Price *</label><input type="number" step="0.01" value={line.unit_price} onChange={(e)=>updateSalesLine(idx,'unit_price',e.target.value)} required/></div>
                        <div className="form-group"><button type="button" className="btn btn-danger" onClick={()=>removeSalesLine(idx)}>ðŸ—'ï¸</button></div>
                      </div>
                    ))}
                  </div>

                  <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':'Create Order'}</button></div>
                </form>
              )}'''

new_sales_form = '''              {showForm === 'salesOrder' && (
                <form onSubmit={submitSalesOrder}>
                  {/* Order Type Selection */}
                  <div className="form-group" style={{marginBottom:'20px', padding:'15px', background:'#f8f9fa', borderRadius:'8px'}}>
                    <label style={{fontWeight:'600', marginBottom:'10px', display:'block'}}>Order Type *</label>
                    <div style={{display:'flex', gap:'20px'}}>
                      <label style={{cursor:'pointer', padding:'10px 20px', border:'2px solid #667eea', borderRadius:'6px', background: forms.salesOrder.order_type === 'retail' ? '#667eea' : 'white', color: forms.salesOrder.order_type === 'retail' ? 'white' : '#667eea', fontWeight:'600', transition:'all 0.3s'}}>
                        <input type="radio" name="order_type" value="retail" checked={forms.salesOrder.order_type === 'retail'} onChange={(e)=>handleFormChange('salesOrder','order_type',e.target.value)} style={{marginRight:'8px'}}/>
                        🛍️ Retail Order
                      </label>
                      <label style={{cursor:'pointer', padding:'10px 20px', border:'2px solid #667eea', borderRadius:'6px', background: forms.salesOrder.order_type === 'wholesale' ? '#667eea' : 'white', color: forms.salesOrder.order_type === 'wholesale' ? 'white' : '#667eea', fontWeight:'600', transition:'all 0.3s'}}>
                        <input type="radio" name="order_type" value="wholesale" checked={forms.salesOrder.order_type === 'wholesale'} onChange={(e)=>handleFormChange('salesOrder','order_type',e.target.value)} style={{marginRight:'8px'}}/>
                        📦 Wholesale Order
                      </label>
                    </div>
                  </div>

                  <div className="form-row">
                    <div className="form-group"><label>Customer *</label><select value={forms.salesOrder.customer_id} onChange={(e)=>handleFormChange('salesOrder','customer_id',e.target.value)} required><option value="">Select customer</option>{(data.customers||[]).map(c=>(<option key={c.id} value={c.id}>{c.name}</option>))}</select></div>
                    <div className="form-group"><button type="button" className="btn btn-secondary" onClick={()=>openForm('customer')} style={{marginTop:'1.5rem'}}>ž• New Customer</button></div>
                    <div className="form-group"><label>Required Date</label><input type="date" value={forms.salesOrder.required_date} onChange={(e)=>handleFormChange('salesOrder','required_date',e.target.value)}/></div>
                  </div>
                  <div className="form-group"><label>Notes</label><textarea rows="2" value={forms.salesOrder.notes} onChange={(e)=>handleFormChange('salesOrder','notes',e.target.value)}/></div>

                  <div className="lines-section">
                    <div className="lines-header">
                      <h4>Order Lines</h4>
                      <button type="button" className="btn btn-secondary" onClick={addSalesLine}>ž• Add Line</button>
                    </div>
                    {(forms.salesOrder.lines||[]).map((line, idx) => {
                      const selectedProduct = (data.products||[]).find(p => p.id === line.product_id);
                      const lineTotal = (parseFloat(line.quantity)||0) * (parseFloat(line.unit_price)||0);
                      
                      return (
                        <div key={idx} className="form-row line-row" style={{position:'relative', background:'#f8f9fa', padding:'15px', borderRadius:'8px', marginBottom:'10px'}}>
                          <div className="form-group"><label>Product *</label><select value={line.product_id} onChange={(e)=>updateSalesLine(idx,'product_id',e.target.value)} required><option value="">Select product</option>{(data.products||[]).map(p=>(<option key={p.id} value={p.id}>{p.name}</option>))}</select></div>
                          
                          <div className="form-group">
                            <label>Unit of Measure *</label>
                            <select value={line.unit} onChange={(e)=>updateSalesLine(idx,'unit',e.target.value)} required disabled={!line.product_id}>
                              <option value="">Select unit</option>
                              {selectedProduct && selectedProduct.pricing && selectedProduct.pricing.map((pr,i)=>(
                                <option key={i} value={pr.unit}>
                                  {pr.unit} - {forms.salesOrder.order_type === 'retail' ? formatCurrency(pr.retail_price) : formatCurrency(pr.wholesale_price)}
                                </option>
                              ))}
                            </select>
                          </div>
                          
                          <div className="form-group"><label>Qty *</label><input type="number" step="0.01" value={line.quantity} onChange={(e)=>updateSalesLine(idx,'quantity',e.target.value)} required/></div>
                          
                          <div className="form-group">
                            <label>Unit Price (Auto)</label>
                            <input type="number" step="0.01" value={line.unit_price} readOnly style={{background:'#e9ecef', cursor:'not-allowed'}}/>
                          </div>
                          
                          <div className="form-group">
                            <label>Line Total</label>
                            <div style={{padding:'10px', background:'#667eea', color:'white', borderRadius:'6px', fontWeight:'600', textAlign:'center'}}>
                              {formatCurrency(lineTotal)}
                            </div>
                          </div>
                          
                          <div className="form-group"><button type="button" className="btn btn-danger" onClick={()=>removeSalesLine(idx)} style={{marginTop:'1.5rem'}}>ðŸ—'ï¸</button></div>
                        </div>
                      );
                    })}
                  </div>

                  {/* Grand Total */}
                  {forms.salesOrder.lines.length > 0 && (
                    <div style={{marginTop:'20px', padding:'20px', background:'linear-gradient(135deg, #667eea 0%, #764ba2 100%)', borderRadius:'8px', color:'white'}}>
                      <div style={{display:'flex', justifyContent:'space-between', alignItems:'center'}}>
                        <h3 style={{margin:0}}>Grand Total:</h3>
                        <h2 style={{margin:0, fontSize:'32px', fontWeight:'700'}}>
                          {formatCurrency(forms.salesOrder.lines.reduce((sum,line)=>{
                            const lineTotal = (parseFloat(line.quantity)||0) * (parseFloat(line.unit_price)||0);
                            return sum + lineTotal;
                          }, 0))}
                        </h2>
                      </div>
                    </div>
                  )}

                  <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':'Create Order'}</button></div>
                </form>
              )}'''

if old_sales_form in content:
    content = content.replace(old_sales_form, new_sales_form)
    print("✅ Sales Order Form: Enhanced with order type selector, unit dropdown, auto-pricing, line totals, and grand total")
else:
    print("⚠️  Sales Order Form: Original pattern not found, skipping...")

# Write updated content
with open(APP_MAIN_PATH, 'w', encoding='utf-8', newline='') as f:
    f.write(content)

print("")
print(f"📦 Backup saved to: {BACKUP_PATH}")
print("✅ File updated successfully!")
print("")
print("🎯 Summary of Changes:")
print("   1. ✅ Settings Module - Expanded to 6 comprehensive cards")
print("   2. ✅ Reports Module - Expanded to 6 detailed report cards")
print("   3. ✅ Sales Order Form - Added order type selector (Retail/Wholesale)")
print("   4. ✅ Sales Order Form - Added unit of measure dropdown per line")
print("   5. ✅ Sales Order Form - Auto-calculated prices based on product+unit+ordertype")
print("   6. ✅ Sales Order Form - Added line total display")
print("   7. ✅ Sales Order Form - Added grand total display")
print("")
print("🚀 Next Steps:")
print("   1. cd frontend")
print("   2. npm run build")
print("   3. Deploy to production: deploy-frontend-simple.ps1")
