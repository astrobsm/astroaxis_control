import React, { useEffect, useState } from 'react';
import './styles.css';

// Build stamp to verify fresh bundle after rebuilds
const BUILD_TAG = 'v2025.10.26-erp-ui-forms';

function App() {
  // Navigation and UI state
  const [activeModule, setActiveModule] = useState('dashboard');
  const [showForm, setShowForm] = useState(''); // which modal form is open
  const [editingItem, setEditingItem] = useState(null);
  const [loading, setLoading] = useState(false);
  const [notifications, setNotifications] = useState([]);
  const [sidebarVisible, setSidebarVisible] = useState(true); // sidebar toggle state

  // Backend data cache
  const [data, setData] = useState({
    staff: [],
    products: [],
    rawMaterials: [],
    stock: [],
    warehouses: [],
    production: [],
    sales: [],
    attendance: [],
    customers: [],
  });
  
  // Birthday notifications
  const [upcomingBirthdays, setUpcomingBirthdays] = useState([]);
  
  // Stock Management state
  const [stockLevels, setStockLevels] = useState({ products: [], rawMaterials: [] });
  const [stockAnalysis, setStockAnalysis] = useState(null);
  const [stockDisplayArea, setStockDisplayArea] = useState('');
  
  // Production & BOM state
  const [productionRequirements, setProductionRequirements] = useState(null);
  const [bomLines, setBomLines] = useState([]);
  const [selectedWarehouse, setSelectedWarehouse] = useState('');

  // Form models
  const [forms, setForms] = useState({
    staff: {
      first_name: '', last_name: '', phone: '', date_of_birth: '',
      position: '', payment_mode: '', hourly_rate: '', monthly_salary: '', hire_date: '',
      bank_name: '', bank_account_number: '', bank_account_name: '', bank_currency: 'NGN'
    },
    product: { 
      sku: '', name: '', unit: 'each', description: '', manufacturer: '',
      reorder_level: '', cost_price: '', selling_price: '', retail_price: '', wholesale_price: '',
      lead_time_days: '', minimum_order_quantity: '1'
    },
    rawMaterial: { name: '', sku: '', manufacturer: '', unit: 'kg', reorder_level: '0', unit_cost: '', date_added: new Date().toISOString().split('T')[0] },
    stockIntake: { product_id: '', warehouse_id: '', quantity: '', unit_cost: '', supplier: '', batch_number: '', notes: '' },
    rawMaterialIntake: { raw_material_id: '', warehouse_id: '', quantity: '', unit_cost: '', supplier: '', batch_number: '', notes: '' },
    damagedProduct: { warehouse_id: '', product_id: '', quantity: '', damage_type: '', damage_reason: '', notes: '' },
    damagedRawMaterial: { warehouse_id: '', raw_material_id: '', quantity: '', damage_type: '', damage_reason: '', notes: '' },
    returnedProduct: { warehouse_id: '', product_id: '', quantity: '', return_reason: '', return_condition: 'good', customer_name: '', refund_amount: '0', notes: '' },
    warehouse: { code: '', name: '', location: '', manager_id: '' },
    customer: { customer_code: '', name: '', email: '', phone: '', address: '', credit_limit: '0' },
    payroll: { staff_id: '', pay_period_start: '', pay_period_end: '' },
    production: { product_id: '', quantity_to_produce: '', warehouse_id: '' }, // console quick action
    productionOrder: { product_id: '', quantity_planned: '', scheduled_start_date: '', scheduled_end_date: '', priority: 5, notes: '' },
    bom: { product_id: '', lines: [] },
    salesOrder: { customer_id: '', required_date: '', notes: '', lines: [] },
  });

  // Helpers
  const notify = (message, type = 'info') => {
    const id = Date.now();
    setNotifications((prev) => [...prev, { id, message, type }]);
    setTimeout(() => setNotifications((prev) => prev.filter((n) => n.id !== id)), 5000);
  };

  const formatCurrency = (n) => `â‚¦${Number(n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

  const extractItems = async (res) => {
    if (!res.ok) throw new Error((await res.json()).detail || 'Request failed');
    const j = await res.json();
    if (j && typeof j === 'object' && Array.isArray(j.items)) return j.items;
    if (Array.isArray(j)) return j;
    return j;
  };

  // Fetch all data on load
  useEffect(() => {
    fetchAllData();
    fetchUpcomingBirthdays();
  }, []);

  async function fetchUpcomingBirthdays() {
    try {
      const res = await fetch('/api/staff/birthdays/upcoming?days_ahead=7');
      if (res.ok) {
        const birthdays = await res.json();
        setUpcomingBirthdays(birthdays || []);
      }
    } catch (e) {
      console.error('Error fetching birthdays:', e);
    }
  }

  async function fetchAllData() {
    try {
      setLoading(true);
      const endpoints = {
        staff: '/api/staff/staffs/',
        products: '/api/products/',
        rawMaterials: '/api/raw-materials/',
        stock: '/api/stock/',
        warehouses: '/api/warehouses/',
        production: '/api/production/orders',
        sales: '/api/sales/orders',
        attendance: '/api/attendance/',
        customers: '/api/sales/customers',
      };

      const entries = await Promise.all(
        Object.entries(endpoints).map(async ([key, url]) => {
          try {
            const res = await fetch(url);
            const items = await extractItems(res);
            return [key, Array.isArray(items) ? items : []];
          } catch (e) {
            console.error(`Error fetching ${key}:`, e);
            return [key, []];
          }
        })
      );

      setData(Object.fromEntries(entries));
    } catch (e) {
      notify(`Error loading data: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  async function fetchData(moduleKey) {
    const map = {
      staff: '/api/staff/staffs/',
      products: '/api/products/',
      rawMaterials: '/api/raw-materials/',
      stock: '/api/stock/',
      warehouses: '/api/warehouses/',
      production: '/api/production/orders',
      sales: '/api/sales/orders',
      attendance: '/api/attendance/',
      customers: '/api/sales/customers',
    };
    try {
      setLoading(true);
      const res = await fetch(map[moduleKey]);
      const items = await extractItems(res);
      setData((prev) => ({ ...prev, [moduleKey]: Array.isArray(items) ? items : [] }));
    } catch (e) {
      notify(`Error loading ${moduleKey}: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  function openForm(formName, item = null) {
    setEditingItem(item);
    setShowForm(formName);
    if (formName === 'staff' && item) {
      setForms((p) => ({ ...p, staff: {
        first_name: item.first_name || '', last_name: item.last_name || '',
        phone: item.phone || '', date_of_birth: item.date_of_birth || '',
        position: item.position || '', payment_mode: item.payment_mode || '', 
        hourly_rate: item.hourly_rate || '', monthly_salary: item.monthly_salary || '',
        hire_date: item.hire_date || '', bank_name: item.bank_name || '',
        bank_account_number: item.bank_account_number || '', bank_account_name: item.bank_account_name || '',
        bank_currency: item.bank_currency || 'NGN'
      }}));
    }
    if (formName === 'product' && item) {
      setForms((p) => ({ ...p, product: { 
        sku: item.sku || '', name: item.name || '', unit: item.unit || 'each', 
        description: item.description || '', manufacturer: item.manufacturer || '',
        reorder_level: item.reorder_level || '', cost_price: item.cost_price || '',
        selling_price: item.selling_price || '', retail_price: item.retail_price || '', wholesale_price: item.wholesale_price || '',
        lead_time_days: item.lead_time_days || '',
        minimum_order_quantity: item.minimum_order_quantity || '1'
      } }));
    }
    if (formName === 'rawMaterial' && item) {
      setForms((p) => ({ ...p, rawMaterial: { sku: item.sku || '', name: item.name || '', unit_cost: item.unit_cost || '' } }));
    }
    if (formName === 'warehouse' && item) {
      setForms((p) => ({ ...p, warehouse: { 
        code: item.code || '', 
        name: item.name || '', 
        location: item.location || '',
        manager_id: item.manager_id || ''
      } }));
    }
    if (formName === 'customer' && item) {
      setForms((p) => ({ ...p, customer: { 
        customer_code: item.customer_code || '', 
        name: item.name || '', 
        email: item.email || '', 
        phone: item.phone || '', 
        address: item.address || '', 
        credit_limit: item.credit_limit || '0' 
      } }));
    }
    if (formName === 'stockIntake' && item) {
      setForms((p) => ({ ...p, stockIntake: { ...p.stockIntake, ...item } }));
    }
    if (formName === 'rawMaterialIntake' && item) {
      setForms((p) => ({ ...p, rawMaterialIntake: { ...p.rawMaterialIntake, ...item } }));
    }
    if (formName === 'payroll' && item && item.staff_id) {
      setForms((p) => ({ ...p, payroll: { ...p.payroll, staff_id: item.staff_id } }));
    }
    if (formName === 'productionOrder' && item) {
      setForms((p) => ({ ...p, productionOrder: {
        product_id: item.product_id || '',
        quantity_planned: item.quantity_planned || item.quantity_to_produce || '',
        scheduled_start_date: item.scheduled_start_date || '',
        scheduled_end_date: item.scheduled_end_date || '',
        priority: item.priority ?? 5,
        notes: item.notes || ''
      }}));
    }
    if (formName === 'salesOrder' && item) {
      setForms((p) => ({ ...p, salesOrder: {
        customer_id: item.customer_id || '',
        required_date: item.required_date ? (item.required_date.substring(0,10)) : '',
        notes: item.notes || '',
        lines: Array.isArray(item.lines) ? item.lines.map(l => ({ product_id: l.product_id || '', quantity: l.quantity || '', unit_price: l.unit_price || '' })) : []
      }}));
    }
  }

  function resetForm(formName) {
    const defaults = {
      staff: {
        first_name: '', last_name: '', phone: '', date_of_birth: '',
        position: '', payment_mode: '', hourly_rate: '', monthly_salary: '', hire_date: '',
        bank_name: '', bank_account_number: '', bank_account_name: '', bank_currency: 'NGN'
      },
      product: { 
        sku: '', name: '', unit: 'each', description: '', manufacturer: '',
        reorder_level: '', cost_price: '', selling_price: '', 
        lead_time_days: '', minimum_order_quantity: '1'
      },
      rawMaterial: { sku: '', name: '', unit_cost: '' },
      stockIntake: { product_id: '', warehouse_id: '', quantity: '', unit_cost: '', supplier: '', batch_number: '', expiry_date: '', notes: '' },
      rawMaterialIntake: { raw_material_id: '', warehouse_id: '', quantity: '', unit_cost: '', supplier: '', batch_number: '', expiry_date: '', notes: '' },
      warehouse: { code: '', name: '', location: '', manager_id: '' },
      customer: { customer_code: '', name: '', email: '', phone: '', address: '', credit_limit: '0' },
      payroll: { staff_id: '', pay_period_start: '', pay_period_end: '' },
      production: { product_id: '', quantity_to_produce: '' },
      productionOrder: { product_id: '', quantity_planned: '', scheduled_start_date: '', scheduled_end_date: '', priority: 5, notes: '' },
      salesOrder: { customer_id: '', required_date: '', notes: '', lines: [] },
    };
    setForms((p) => ({ ...p, [formName]: defaults[formName] }));
  }

  function handleFormChange(formName, field, value) {
    setForms((prev) => ({ ...prev, [formName]: { ...prev[formName], [field]: value } }));
  }

  // Sales Order line helpers
  function addSalesLine() {
    setForms((p) => ({ ...p, salesOrder: { ...p.salesOrder, lines: [...p.salesOrder.lines, { product_id: '', quantity: '', unit_price: '' }] } }));
  }
  function updateSalesLine(index, field, value) {
    setForms((p) => {
      const lines = [...p.salesOrder.lines];
      lines[index] = { ...lines[index], [field]: value };
      return { ...p, salesOrder: { ...p.salesOrder, lines } };
    });
  }
  function removeSalesLine(index) {
    setForms((p) => {
      const lines = [...p.salesOrder.lines];
      lines.splice(index, 1);
      return { ...p, salesOrder: { ...p.salesOrder, lines } };
    });
  }

  async function saveItem(entity, endpoint) {
    try {
      setLoading(true);
      let payload = {};
      if (entity === 'staff') payload = { ...forms.staff };
      if (entity === 'product') payload = { ...forms.product };
      if (entity === 'rawMaterial') payload = { ...forms.rawMaterial };
      if (entity === 'warehouse') payload = { ...forms.warehouse };

      // Clean payload: convert empty strings to null for optional fields, remove empty numeric fields
      Object.keys(payload).forEach(key => {
        if (payload[key] === '' && !['first_name', 'last_name', 'name', 'sku', 'code'].includes(key)) {
          payload[key] = null;
        }
        // Remove empty numeric fields like hourly_rate, monthly_salary
        if (['hourly_rate', 'monthly_salary', 'cost_price', 'selling_price', 'retail_price', 'wholesale_price', 'unit_cost', 'reorder_level'].includes(key) && (payload[key] === '' || payload[key] === null)) {
          delete payload[key];
        }
      });

      const method = editingItem?.id ? 'PUT' : 'POST';
      const url = editingItem?.id ? `${endpoint}${editingItem.id}` : endpoint;

      const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (!res.ok) throw new Error((await res.json()).detail || 'Save failed');
      notify(`${entity} saved`, 'success');
      setShowForm('');
      setEditingItem(null);
      // Refresh relevant module
      const moduleKey = entity === 'rawMaterial' ? 'rawMaterials' : (entity === 'product' ? 'products' : (entity === 'staff' ? 'staff' : 'warehouses'));
      fetchData(moduleKey);
    } catch (e) {
      notify(`Error saving ${entity}: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  // Production console quick action
  async function processProduction(productId, qty) {
    if (!productId || !qty) return;
    try {
      setLoading(true);
      const url = `/api/production/execute-production?product_id=${encodeURIComponent(productId)}&quantity=${encodeURIComponent(qty)}`;
      const res = await fetch(url, { method: 'POST' });
      if (!res.ok) throw new Error((await res.json()).detail || 'Production failed');
      const j = await res.json();
      notify(j.message || 'Production executed', 'success');
      fetchData('production');
      fetchData('stock');
    } catch (e) {
      notify(`Production error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  async function generateInvoice(orderId) {
    try {
      setLoading(true);
      const res = await fetch(`/api/sales/generate-invoice-pdf/${orderId}`);
      if (!res.ok) throw new Error('Failed to generate invoice');
      const blob = await res.blob();
      const url = window.URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `invoice_${orderId}.pdf`; document.body.appendChild(a); a.click(); a.remove();
      window.URL.revokeObjectURL(url);
      notify('Invoice downloaded', 'success');
    } catch (e) {
      notify(`Invoice error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  async function processOrder(orderId) {
    try {
      setLoading(true);
      const res = await fetch(`/api/sales/process-order/${orderId}`, { method: 'POST' });
      if (!res.ok) throw new Error((await res.json()).detail || 'Failed to process order');
      const j = await res.json();
      notify(j.message || 'Order processed', 'success');
      fetchData('sales');
      fetchData('stock');
    } catch (e) {
      notify(`Process order error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  // Sales Order submit
  async function submitSalesOrder(e) {
    e.preventDefault();
    try {
      if (!forms.salesOrder.customer_id) throw new Error('Customer required');
      if (!forms.salesOrder.lines.length) throw new Error('Add at least one line');
      setLoading(true);
      const payload = {
        customer_id: forms.salesOrder.customer_id,
        required_date: forms.salesOrder.required_date ? `${forms.salesOrder.required_date}T00:00:00` : null,
        notes: forms.salesOrder.notes || null,
        lines: forms.salesOrder.lines.map(l => ({
          product_id: l.product_id,
          quantity: Number(l.quantity || 0),
          unit_price: Number(l.unit_price || 0),
        }))
      };
      const res = await fetch('/api/sales/orders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (!res.ok) throw new Error((await res.json()).detail || 'Failed to create order');
      notify('Sales order created', 'success');
      setShowForm('');
      resetForm('salesOrder');
      fetchData('sales');
    } catch (e) {
      notify(`Sales order error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  // Production Order submit
  async function submitProductionOrder(e) {
    e.preventDefault();
    try {
      if (!forms.productionOrder.product_id || !forms.productionOrder.quantity_planned) throw new Error('Product and quantity required');
      setLoading(true);
      const payload = {
        product_id: forms.productionOrder.product_id,
        quantity_planned: Number(forms.productionOrder.quantity_planned),
        scheduled_start_date: forms.productionOrder.scheduled_start_date ? `${forms.productionOrder.scheduled_start_date}T00:00:00` : null,
        scheduled_end_date: forms.productionOrder.scheduled_end_date ? `${forms.productionOrder.scheduled_end_date}T00:00:00` : null,
        priority: Number(forms.productionOrder.priority || 5),
        notes: forms.productionOrder.notes || null,
      };
      const res = await fetch('/api/production/orders', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (!res.ok) throw new Error((await res.json()).detail || 'Failed to create production order');
      notify('Production order created', 'success');
      setShowForm('');
      resetForm('productionOrder');
      fetchData('production');
    } catch (e) {
      notify(`Production order error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  // Payroll submit (calculate + auto-download payslip by payroll_id)
  async function submitPayroll(e) {
    e.preventDefault();
    try {
      if (!forms.payroll.staff_id || !forms.payroll.pay_period_start || !forms.payroll.pay_period_end) throw new Error('Complete the fields');
      setLoading(true);
      const payload = { ...forms.payroll };
      const res = await fetch('/api/staff/payroll/calculate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      if (!res.ok) throw new Error((await res.json()).detail || 'Failed to calculate payroll');
      const payroll = await res.json();
      notify('Payroll calculated', 'success');
      // Auto download payslip by payroll_id
      const pdf = await fetch(`/api/staff/payslip/${payroll.id}/pdf`);
      if (pdf.ok) {
        const blob = await pdf.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a'); a.href = url; a.download = `payslip_${payroll.id}.pdf`; document.body.appendChild(a); a.click(); a.remove();
        window.URL.revokeObjectURL(url);
        notify('Payslip downloaded', 'success');
      }
      setShowForm('');
      resetForm('payroll');
    } catch (e) {
      notify(`Payroll error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  // ==================== BOM & PRODUCTION REQUIREMENT FUNCTIONS ====================
  
  // Calculate production requirements
  async function calculateProductionRequirements() {
    try {
      if (!forms.production.product_id || !forms.production.quantity_to_produce) {
        notify('Please select product and quantity', 'error');
        return;
      }
      
      if (!forms.production.warehouse_id) {
        notify('Please select warehouse', 'error');
        return;
      }
      
      setLoading(true);
      const url = `/api/bom/calculate-requirements?product_id=${forms.production.product_id}&quantity=${forms.production.quantity_to_produce}&warehouse_id=${forms.production.warehouse_id}`;
      const res = await fetch(url);
      
      if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail || 'Failed to calculate requirements');
      }
      
      const requirements = await res.json();
      setProductionRequirements(requirements);
      
      if (requirements.can_produce) {
        notify(`Requirements calculated: â‚¦${requirements.total_material_cost.toFixed(2)} material cost`, 'success');
      } else {
        notify('âš ï¸ Insufficient stock! Check shortages below', 'warning');
      }
    } catch (e) {
      notify(`Error: ${e.message}`, 'error');
      setProductionRequirements(null);
    } finally {
      setLoading(false);
    }
  }
  
  // Approve production and deduct stock
  async function approveProductionAndDeduct() {
    try {
      if (!productionRequirements) {
        notify('Calculate requirements first', 'error');
        return;
      }
      
      if (!productionRequirements.can_produce) {
        notify('Cannot approve production due to stock shortages', 'error');
        return;
      }
      
      setLoading(true);
      const payload = {
        product_id: productionRequirements.product_id,
        quantity: productionRequirements.quantity_to_produce,
        warehouse_id: productionRequirements.warehouse_id,
        notes: `Production approved via console on ${new Date().toLocaleString()}`
      };
      
      const res = await fetch('/api/bom/approve-production', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      
      if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail?.error || error.detail || 'Failed to approve production');
      }
      
      const result = await res.json();
      notify(`âœ… ${result.message}`, 'success');
      
      // Reset production form and requirements
      resetForm('production');
      setProductionRequirements(null);
      
      // Refresh data
      fetchAllData();
    } catch (e) {
      notify(`Production approval error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }
  
  // Create/Update BOM
  async function saveBOM() {
    try {
      if (!forms.bom.product_id || bomLines.length === 0) {
        notify('Please select product and add at least one raw material', 'error');
        return;
      }
      
      setLoading(true);
      const payload = {
        product_id: forms.bom.product_id,
        lines: bomLines.map(line => ({
          raw_material_id: line.raw_material_id,
          qty_per_unit: parseFloat(line.qty_per_unit),
          unit: line.unit
        }))
      };
      
      const res = await fetch('/api/bom/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      
      if (!res.ok) {
        const error = await res.json();
        throw new Error(error.detail || 'Failed to save BOM');
      }
      
      const result = await res.json();
      notify(result.message, 'success');
      
      // Reset form
      setShowForm('');
      resetForm('bom');
      setBomLines([]);
      fetchAllData();
    } catch (e) {
      notify(`BOM save error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }
  
  // Add BOM line
  function addBomLine() {
    setBomLines([...bomLines, { id: Date.now(), raw_material_id: '', qty_per_unit: '', unit: 'kg' }]);
  }
  
  // Remove BOM line
  function removeBomLine(lineId) {
    setBomLines(bomLines.filter(line => line.id !== lineId));
  }
  
  // Update BOM line
  function updateBomLine(lineId, field, value) {
    setBomLines(bomLines.map(line => 
      line.id === lineId ? { ...line, [field]: value } : line
    ));
  }
  
  // View product BOM
  async function viewProductBOM(productId) {
    try {
      setLoading(true);
      const res = await fetch(`/api/bom/product/${productId}`);
      
      if (!res.ok) {
        throw new Error('Failed to fetch BOM');
      }
      
      const bom = await res.json();
      
      if (!bom.has_bom) {
        notify('No BOM defined for this product', 'info');
        return;
      }
      
      // Display BOM details
      let html = `
        <div class="bom-details">
          <h3>ðŸ“‹ Bill of Materials: ${bom.product_name}</h3>
          <p><strong>Product SKU:</strong> ${bom.product_sku}</p>
          <p><strong>BOM Version:</strong> ${bom.version}</p>
          <p><strong>Raw Materials:</strong> ${bom.lines_count}</p>
          <p><strong>Total Material Cost per Unit:</strong> ${formatCurrency(bom.total_material_cost)}</p>
          
          <table class="stock-table">
            <thead>
              <tr>
                <th>Raw Material</th>
                <th>SKU</th>
                <th>Qty per Unit</th>
                <th>Unit</th>
                <th>Unit Cost</th>
                <th>Line Cost</th>
              </tr>
            </thead>
            <tbody>
              ${bom.lines.map(line => `
                <tr>
                  <td>${line.raw_material_name}</td>
                  <td>${line.raw_material_sku}</td>
                  <td>${line.qty_per_unit}</td>
                  <td>${line.unit}</td>
                  <td>${formatCurrency(line.unit_cost)}</td>
                  <td><strong>${formatCurrency(line.line_cost)}</strong></td>
                </tr>
              `).join('')}
            </tbody>
            <tfoot>
              <tr>
                <td colspan="5" style="text-align: right;"><strong>Total Material Cost:</strong></td>
                <td><strong>${formatCurrency(bom.total_material_cost)}</strong></td>
              </tr>
            </tfoot>
          </table>
        </div>
      `;
      
      setStockDisplayArea(html);
    } catch (e) {
      notify(`Error fetching BOM: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  // ==================== STOCK MANAGEMENT FUNCTIONS ====================
  
  // Fetch stock levels
  async function fetchStockLevels(type = 'all') {
    try {
      setLoading(true);
      if (type === 'products' || type === 'all') {
        const res = await fetch('/api/stock-management/product-levels');
        if (res.ok) {
          const productLevels = await res.json();
          setStockLevels(prev => ({ ...prev, products: productLevels }));
        }
      }
      if (type === 'rawMaterials' || type === 'all') {
        const res = await fetch('/api/stock-management/raw-material-levels');
        if (res.ok) {
          const rawMaterialLevels = await res.json();
          setStockLevels(prev => ({ ...prev, rawMaterials: rawMaterialLevels }));
        }
      }
    } catch (e) {
      notify(`Error fetching stock levels: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  // Fetch stock analysis
  async function fetchStockAnalysis() {
    try {
      setLoading(true);
      const res = await fetch('/api/stock-management/analysis');
      if (res.ok) {
        const analysis = await res.json();
        setStockAnalysis(analysis);
      }
    } catch (e) {
      notify(`Error fetching stock analysis: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  // Submit product stock intake
  async function submitProductStockIntake(e) {
    e.preventDefault();
    try {
      if (!forms.stockIntake.warehouse_id || !forms.stockIntake.product_id || !forms.stockIntake.quantity || !forms.stockIntake.unit_cost) {
        throw new Error('Please fill all required fields');
      }
      setLoading(true);
      const payload = {
        warehouse_id: forms.stockIntake.warehouse_id,
        product_id: forms.stockIntake.product_id,
        quantity: parseFloat(forms.stockIntake.quantity),
        unit_cost: parseFloat(forms.stockIntake.unit_cost),
        supplier: forms.stockIntake.supplier || null,
        batch_number: forms.stockIntake.batch_number || null,
        notes: forms.stockIntake.notes || null,
      };
      const res = await fetch('/api/stock-management/product-intake', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error((await res.json()).detail || 'Failed to record product intake');
      const result = await res.json();
      notify(result.message, 'success');
      setShowForm('');
      resetForm('stockIntake');
      fetchStockLevels('products');
    } catch (e) {
      notify(`Product intake error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  // Submit raw material stock intake
  async function submitRawMaterialStockIntake(e) {
    e.preventDefault();
    try {
      if (!forms.rawMaterialIntake.warehouse_id || !forms.rawMaterialIntake.raw_material_id || !forms.rawMaterialIntake.quantity || !forms.rawMaterialIntake.unit_cost) {
        throw new Error('Please fill all required fields');
      }
      setLoading(true);
      const payload = {
        warehouse_id: forms.rawMaterialIntake.warehouse_id,
        raw_material_id: forms.rawMaterialIntake.raw_material_id,
        quantity: parseFloat(forms.rawMaterialIntake.quantity),
        unit_cost: parseFloat(forms.rawMaterialIntake.unit_cost),
        supplier: forms.rawMaterialIntake.supplier || null,
        batch_number: forms.rawMaterialIntake.batch_number || null,
        notes: forms.rawMaterialIntake.notes || null,
      };
      const res = await fetch('/api/stock-management/raw-material-intake', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error((await res.json()).detail || 'Failed to record raw material intake');
      const result = await res.json();
      notify(result.message, 'success');
      setShowForm('');
      resetForm('rawMaterialIntake');
      fetchStockLevels('rawMaterials');
    } catch (e) {
      notify(`Raw material intake error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  // Submit damaged product
  async function submitDamagedProduct(e) {
    e.preventDefault();
    try {
      if (!forms.damagedProduct.warehouse_id || !forms.damagedProduct.product_id || !forms.damagedProduct.quantity || !forms.damagedProduct.damage_type) {
        throw new Error('Please fill all required fields');
      }
      setLoading(true);
      const payload = {
        warehouse_id: forms.damagedProduct.warehouse_id,
        product_id: forms.damagedProduct.product_id,
        quantity: parseFloat(forms.damagedProduct.quantity),
        damage_type: forms.damagedProduct.damage_type,
        damage_reason: forms.damagedProduct.damage_reason || null,
        notes: forms.damagedProduct.notes || null,
      };
      const res = await fetch('/api/stock-management/damaged-product', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error((await res.json()).detail || 'Failed to record damaged product');
      const result = await res.json();
      notify(result.message, 'success');
      setShowForm('');
      resetForm('damagedProduct');
      fetchStockLevels('products');
    } catch (e) {
      notify(`Damaged product error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  // Submit damaged raw material
  async function submitDamagedRawMaterial(e) {
    e.preventDefault();
    try {
      if (!forms.damagedRawMaterial.warehouse_id || !forms.damagedRawMaterial.raw_material_id || !forms.damagedRawMaterial.quantity || !forms.damagedRawMaterial.damage_type) {
        throw new Error('Please fill all required fields');
      }
      setLoading(true);
      const payload = {
        warehouse_id: forms.damagedRawMaterial.warehouse_id,
        raw_material_id: forms.damagedRawMaterial.raw_material_id,
        quantity: parseFloat(forms.damagedRawMaterial.quantity),
        damage_type: forms.damagedRawMaterial.damage_type,
        damage_reason: forms.damagedRawMaterial.damage_reason || null,
        notes: forms.damagedRawMaterial.notes || null,
      };
      const res = await fetch('/api/stock-management/damaged-raw-material', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error((await res.json()).detail || 'Failed to record damaged raw material');
      const result = await res.json();
      notify(result.message, 'success');
      setShowForm('');
      resetForm('damagedRawMaterial');
      fetchStockLevels('rawMaterials');
    } catch (e) {
      notify(`Damaged raw material error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  // Submit returned product
  async function submitReturnedProduct(e) {
    e.preventDefault();
    try {
      if (!forms.returnedProduct.warehouse_id || !forms.returnedProduct.product_id || !forms.returnedProduct.quantity || !forms.returnedProduct.return_reason || !forms.returnedProduct.return_condition) {
        throw new Error('Please fill all required fields');
      }
      setLoading(true);
      const payload = {
        warehouse_id: forms.returnedProduct.warehouse_id,
        product_id: forms.returnedProduct.product_id,
        quantity: parseFloat(forms.returnedProduct.quantity),
        return_reason: forms.returnedProduct.return_reason,
        return_condition: forms.returnedProduct.return_condition,
        customer_name: forms.returnedProduct.customer_name || null,
        refund_amount: parseFloat(forms.returnedProduct.refund_amount) || 0,
        notes: forms.returnedProduct.notes || null,
      };
      const res = await fetch('/api/stock-management/returned-product', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error((await res.json()).detail || 'Failed to record product return');
      const result = await res.json();
      notify(result.message, 'success');
      setShowForm('');
      resetForm('returnedProduct');
      fetchStockLevels('products');
    } catch (e) {
      notify(`Product return error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  // View product stock levels
  async function viewProductStockLevels(lowStockOnly = false) {
    try {
      setLoading(true);
      const url = lowStockOnly ? '/api/stock-management/product-levels?low_stock_only=true' : '/api/stock-management/product-levels';
      const res = await fetch(url);
      if (!res.ok) throw new Error('Failed to fetch product stock levels');
      const levels = await res.json();
      
      let html = `
        <div class="stock-levels-section">
          <h3>${lowStockOnly ? 'âš ï¸ Low Stock Products' : 'ðŸ“¦ Product Stock Levels'}</h3>
          <table class="stock-table">
            <thead>
              <tr>
                <th>Product</th>
                <th>SKU</th>
                <th>Warehouse</th>
                <th>Current Stock</th>
                <th>Reserved</th>
                <th>Available</th>
                <th>Reorder Level</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              ${levels.map(l => `
                <tr class="${l.is_low_stock ? 'low-stock-row' : ''}">
                  <td>${l.product_name}</td>
                  <td>${l.product_sku}</td>
                  <td>${l.warehouse_name}</td>
                  <td>${l.current_stock}</td>
                  <td>${l.reserved_stock}</td>
                  <td><strong>${l.available_stock}</strong></td>
                  <td>${l.reorder_level}</td>
                  <td>${l.is_low_stock ? '<span class="status-badge low-stock">LOW STOCK</span>' : '<span class="status-badge ok-stock">OK</span>'}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      `;
      setStockDisplayArea(html);
    } catch (e) {
      notify(`Error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  // View raw material stock levels
  async function viewRawMaterialStockLevels(lowStockOnly = false) {
    try {
      setLoading(true);
      const url = lowStockOnly ? '/api/stock-management/raw-material-levels?low_stock_only=true' : '/api/stock-management/raw-material-levels';
      const res = await fetch(url);
      if (!res.ok) throw new Error('Failed to fetch raw material stock levels');
      const levels = await res.json();
      
      let html = `
        <div class="stock-levels-section">
          <h3>${lowStockOnly ? 'âš ï¸ Low Stock Raw Materials' : 'ðŸ§ª Raw Material Stock Levels'}</h3>
          <table class="stock-table">
            <thead>
              <tr>
                <th>Raw Material</th>
                <th>SKU</th>
                <th>Warehouse</th>
                <th>Current Stock</th>
                <th>Reserved</th>
                <th>Available</th>
                <th>Unit</th>
                <th>Reorder Level</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              ${levels.map(l => `
                <tr class="${l.is_low_stock ? 'low-stock-row' : ''}">
                  <td>${l.raw_material_name}</td>
                  <td>${l.raw_material_sku}</td>
                  <td>${l.warehouse_name}</td>
                  <td>${l.current_stock}</td>
                  <td>${l.reserved_stock}</td>
                  <td><strong>${l.available_stock}</strong></td>
                  <td>${l.unit}</td>
                  <td>${l.reorder_level}</td>
                  <td>${l.is_low_stock ? '<span class="status-badge low-stock">LOW STOCK</span>' : '<span class="status-badge ok-stock">OK</span>'}</td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        </div>
      `;
      setStockDisplayArea(html);
    } catch (e) {
      notify(`Error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  // View stock analysis
  async function viewStockAnalysis() {
    try {
      setLoading(true);
      const res = await fetch('/api/stock-management/analysis');
      if (!res.ok) throw new Error('Failed to fetch stock analysis');
      const analysis = await res.json();
      
      let html = `
        <div class="stock-analysis-section">
          <h3>ðŸ“Š Stock Analysis Dashboard</h3>
          <div class="analysis-stats">
            <div class="stat-card">
              <h4>Product Items</h4>
              <p class="stat-number">${analysis.summary.total_product_items}</p>
            </div>
            <div class="stat-card">
              <h4>Raw Material Items</h4>
              <p class="stat-number">${analysis.summary.total_raw_material_items}</p>
            </div>
            <div class="stat-card warning">
              <h4>Low Stock Products</h4>
              <p class="stat-number">${analysis.summary.low_stock_products}</p>
            </div>
            <div class="stat-card warning">
              <h4>Low Stock Raw Materials</h4>
              <p class="stat-number">${analysis.summary.low_stock_raw_materials}</p>
            </div>
            <div class="stat-card danger">
              <h4>Damaged Items (30d)</h4>
              <p class="stat-number">${analysis.summary.damaged_items_30_days}</p>
            </div>
            <div class="stat-card info">
              <h4>Returns (30d)</h4>
              <p class="stat-number">${analysis.summary.returned_items_30_days}</p>
            </div>
            <div class="stat-card success">
              <h4>Total Product Value</h4>
              <p class="stat-number">${formatCurrency(analysis.summary.total_product_value)}</p>
            </div>
            <div class="stat-card success">
              <h4>Total Raw Material Value</h4>
              <p class="stat-number">${formatCurrency(analysis.summary.total_raw_material_value)}</p>
            </div>
            <div class="stat-card primary">
              <h4>Total Stock Value</h4>
              <p class="stat-number">${formatCurrency(analysis.summary.total_stock_value)}</p>
            </div>
          </div>
        </div>
      `;
      setStockDisplayArea(html);
    } catch (e) {
      notify(`Error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }

  // UI render
  return (
    <div className="app-container">
      {/* Vertical Sidebar Navigation */}
      <aside className={`sidebar ${sidebarVisible ? 'visible' : 'hidden'}`}>
        <div className="sidebar-brand">
          <img 
            src="/company-logo.png" 
            alt="ASTRO-ASIX" 
            className="sidebar-logo"
            onError={(e) => { e.target.style.display = 'none'; }}
          />
          <h1 className="sidebar-title">ASTRO-ASIX</h1>
          <small className="build-badge">{BUILD_TAG}</small>
        </div>
        <nav className="sidebar-nav">
          {['dashboard','staff','attendance','products','rawMaterials','stockManagement','production','sales','reports','settings'].map(m => (
            <button key={m} className={`sidebar-btn ${activeModule===m?'active':''}`} onClick={() => setActiveModule(m)}>
              {m === 'rawMaterials' ? 'RAW MATERIALS' : m === 'stockManagement' ? 'STOCK MANAGEMENT' : m.toUpperCase()}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <button className="btn btn-refresh" onClick={fetchAllData}>â†» Refresh</button>
        </div>
      </aside>

      {/* Sidebar Toggle Button */}
      <button 
        className="sidebar-toggle" 
        onClick={() => setSidebarVisible(!sidebarVisible)}
        title={sidebarVisible ? 'Hide Sidebar' : 'Show Sidebar'}
      >
        {sidebarVisible ? 'â—€' : 'â–¶'}
      </button>

      {/* Main Content Area */}
      <div className={`main-wrapper ${sidebarVisible ? '' : 'expanded'}`}>

      {/* Notifications */}
      {notifications.length > 0 && (
        <div className="notifications-panel">
          {notifications.map(n => (
            <div key={n.id} className={`notification ${n.type}`}>
              <span>{n.message}</span>
              <button onClick={() => setNotifications((p)=>p.filter(x=>x.id!==n.id))}>âœ•</button>
            </div>
          ))}
        </div>
      )}

      <main className="main">
        {/* Dashboard */}
        {activeModule === 'dashboard' && (
          <div className="dashboard">
            <div className="dashboard-header">
              <img src="/company-logo.png" alt="ASTRO-ASIX" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
              <h2>ðŸ“Š Dashboard</h2>
              <p>Comprehensive overview for operations</p>
            </div>
            
            {/* Birthday Notifications */}
            {upcomingBirthdays.length > 0 && (
              <div className="birthday-notifications">
                <h3>ðŸŽ‚ Upcoming Birthdays</h3>
                {upcomingBirthdays.map(birthday => (
                  <div key={birthday.staff_id} className={`birthday-card ${birthday.is_today ? 'birthday-today' : ''}`}>
                    {birthday.is_today ? (
                      <div className="birthday-message">
                        <span className="birthday-icon">ðŸŽ‰</span>
                        <strong>Happy Birthday, {birthday.first_name} {birthday.last_name}!</strong>
                        <span className="birthday-age">Turning {birthday.age_turning} today</span>
                      </div>
                    ) : (
                      <div className="birthday-message">
                        <span className="birthday-icon">ðŸŽ‚</span>
                        <strong>{birthday.first_name} {birthday.last_name}</strong>
                        <span className="birthday-date">Birthday in {birthday.days_until} day{birthday.days_until > 1 ? 's' : ''} (Age {birthday.age_turning})</span>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
            
            <div className="dashboard-stats">
              <div className="stat-card"><h3>Total Staff</h3><p className="stat-number">{data.staff.length}</p></div>
              <div className="stat-card"><h3>Products</h3><p className="stat-number">{data.products.length}</p></div>
              <div className="stat-card"><h3>Active Orders</h3><p className="stat-number">{(data.sales || []).filter(o=>o.status==='pending').length}</p></div>
              <div className="stat-card"><h3>Warehouses</h3><p className="stat-number">{data.warehouses.length}</p></div>
            </div>
            <div className="quick-actions">
              <h3>Quick Actions</h3>
              <div className="action-buttons">
                <button onClick={() => setActiveModule('sales')} className="action-btn">ðŸ’° New Sales Order</button>
                <button onClick={() => setActiveModule('production')} className="action-btn">ðŸ­ Production Console</button>
                <button onClick={() => setActiveModule('attendance')} className="action-btn">ðŸ•’ Clock In/Out</button>
              </div>
            </div>
          </div>
        )}

        {/* Staff */}
        {activeModule === 'staff' && (
          <div className="module-content">
            <div className="module-header">
              <div className="module-header-left">
                <img src="/company-logo.png" alt="ASTRO-ASIX" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
                <h2>ðŸ‘¨â€ðŸ’¼ Staff Management</h2>
              </div>
              <div className="module-actions">
                <button onClick={() => openForm('staff')} className="btn btn-primary">âž• Add Staff</button>
                <button onClick={() => openForm('payroll')} className="btn btn-secondary">ðŸ’° Process Payroll</button>
              </div>
            </div>

            <div className="table-container">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Employee ID</th><th>Name</th><th>Position</th><th>Department</th><th>Hourly Rate</th><th>Bank Details</th><th>Status</th><th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.staff || []).map(staff => (
                    <tr key={staff.id}>
                      <td>{staff.employee_id}</td>
                      <td>{staff.first_name} {staff.last_name}</td>
                      <td>{staff.position}</td>
                      <td>{staff.department}</td>
                      <td>{formatCurrency(staff.hourly_rate)}</td>
                      <td>{staff.bank_name}<br/><small>{staff.account_number}</small></td>
                      <td><span className={`status ${staff.is_active ? 'active' : 'inactive'}`}>{staff.is_active ? 'Active' : 'Inactive'}</span></td>
                      <td className="actions">
                        <button onClick={() => openForm('staff', staff)} className="btn-edit">âœï¸</button>
                        <button onClick={() => openForm('payroll', { staff_id: staff.id })} className="btn-download">ðŸ“„</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Attendance */}
        {activeModule === 'attendance' && (
          <div className="module-content">
            <div className="module-header">
              <div className="module-header-left">
                <img src="/company-logo.png" alt="ASTRO-ASIX" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
                <h2>ðŸ•’ Attendance Management</h2>
              </div>
              <button onClick={() => fetchData('attendance')} className="btn btn-secondary">ðŸ”„ Refresh</button>
            </div>
            
            {/* Clock PIN Input */}
            <div className="attendance-pin-section">
              <h3>ðŸ”¢ Clock In/Out with PIN</h3>
              <div className="pin-input-container">
                <input 
                  type="password" 
                  placeholder="Enter your 4-digit Clock PIN" 
                  maxLength="4"
                  className="pin-input"
                  id="clockPinInput"
                />
                <div className="pin-buttons">
                  <button className="btn btn-success" onClick={() => handlePinClock('clock_in')}>â±ï¸ Clock In</button>
                  <button className="btn btn-warning" onClick={() => handlePinClock('clock_out')}>â¹ï¸ Clock Out</button>
                </div>
              </div>
              <p className="pin-hint">Enter your 4-digit PIN and click Clock In or Clock Out</p>
            </div>
            
            {/* Action Buttons */}
            <div className="attendance-actions">
              <button className="btn btn-primary" onClick={() => viewAttendanceStatus()}>ðŸ‘¥ View Status</button>
              <button className="btn btn-secondary" onClick={() => viewDetailedLog()}>ðŸ“‹ Detailed Log</button>
              <button className="btn btn-success" onClick={() => viewBestPerformers()}>ðŸ† Best Performers</button>
            </div>
            
            {/* Status Display Area */}
            <div id="attendanceDisplayArea" className="attendance-display-area"></div>
          </div>
        )}

        {/* Products */}
        {activeModule === 'products' && (
          <div className="module-content">
            <div className="module-header">
              <div className="module-header-left">
                <img src="/company-logo.png" alt="ASTRO-ASIX" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
                <h2>ðŸ“¦ Products</h2>
              </div>
              <button onClick={() => openForm('product')} className="btn btn-primary">âž• Add Product</button>
            </div>
            <div className="table-container">
              <table className="data-table">
                <thead><tr><th>Name</th><th>Unit</th><th>Description</th><th>Actions</th></tr></thead>
                <tbody>
                  {(data.products || []).map(product => (
                    <tr key={product.id}>
                      <td>{product.name}</td>
                      <td>{product.unit}</td>
                      <td>{product.description}</td>
                      <td className="actions">
                        <button onClick={() => openForm('product', product)} className="btn-edit">âœï¸</button>
                        <button onClick={() => openForm('stockIntake', { product_id: product.id })} className="btn-add">ðŸ“¥</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Raw Materials */}
        {activeModule === 'rawMaterials' && (
          <div className="module-content">
            <div className="module-header">
              <div className="module-header-left">
                <img src="/company-logo.png" alt="ASTRO-ASIX" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
                <h2>ðŸ§± Raw Materials</h2>
              </div>
              <div className="module-actions">
                <button onClick={() => openForm('rawMaterial')} className="btn btn-primary">âž• Add Raw Material</button>
                <button onClick={() => openForm('rawMaterialIntake')} className="btn btn-secondary">ðŸ“¥ Stock Intake</button>
              </div>
            </div>
          </div>
        )}

        {/* Production */}
        {activeModule === 'production' && (
          <div className="module-content">
            <div className="module-header">
              <div className="module-header-left">
                <img src="/company-logo.png" alt="ASTRO-ASIX" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
                <h2>ðŸ­ Production</h2>
              </div>
              <div className="module-header-actions">
                <button onClick={() => { setShowForm('bom'); setBomLines([{ id: Date.now(), raw_material_id: '', qty_per_unit: '', unit: 'kg' }]); }} className="btn btn-secondary">ðŸ“‹ Register BOM</button>
                <button onClick={() => setShowForm('productionOrder')} className="btn btn-primary">âž• New Production Order</button>
              </div>
            </div>
            
            {/* Production Console with Requirements Calculator */}
            <div className="production-console">
              <h3>ðŸŽ¯ Production Console - Material Requirements Calculator</h3>
              <div className="console-form">
                <div className="form-row">
                  <div className="form-group">
                    <label>Select Product *</label>
                    <select value={forms.production.product_id} onChange={(e)=>{ handleFormChange('production','product_id',e.target.value); setProductionRequirements(null); }}>
                      <option value="">Choose Product</option>
                      {(data.products||[]).map(p => (<option key={p.id} value={p.id}>{p.name} - {p.sku}</option>))}
                    </select>
                  </div>
                  <div className="form-group">
                    <label>Quantity to Produce *</label>
                    <input type="number" step="0.01" value={forms.production.quantity_to_produce} onChange={(e)=>{ handleFormChange('production','quantity_to_produce',e.target.value); setProductionRequirements(null); }} placeholder="Enter quantity"/>
                  </div>
                  <div className="form-group">
                    <label>Warehouse *</label>
                    <select value={forms.production.warehouse_id} onChange={(e)=>{ handleFormChange('production','warehouse_id',e.target.value); setProductionRequirements(null); }}>
                      <option value="">Select Warehouse</option>
                      {(data.warehouses||[]).map(w => (<option key={w.id} value={w.id}>{w.name}</option>))}
                    </select>
                  </div>
                </div>
                <div className="form-row">
                  <div className="form-group">
                    <button 
                      className="btn btn-info" 
                      disabled={!forms.production.product_id || !forms.production.quantity_to_produce || !forms.production.warehouse_id}
                      onClick={calculateProductionRequirements}
                      style={{ marginRight: '10px' }}
                    >
                      ðŸ§® Calculate Requirements
                    </button>
                    {forms.production.product_id && (
                      <button 
                        className="btn btn-secondary" 
                        onClick={() => viewProductBOM(forms.production.product_id)}
                      >
                        ðŸ“‹ View BOM
                      </button>
                    )}
                  </div>
                </div>
              </div>
              
              {/* Requirements Display */}
              {productionRequirements && (
                <div className="requirements-display">
                  <h4>ðŸ“Š Material Requirements for {productionRequirements.quantity_to_produce} units of {productionRequirements.product_name}</h4>
                  
                  <div className="requirements-summary">
                    <div className="summary-card">
                      <span className="label">Total Material Cost:</span>
                      <span className="value">{formatCurrency(productionRequirements.total_material_cost)}</span>
                    </div>
                    <div className="summary-card">
                      <span className="label">Cost per Unit:</span>
                      <span className="value">{formatCurrency(productionRequirements.cost_per_unit)}</span>
                    </div>
                    <div className={`summary-card ${productionRequirements.can_produce ? 'success' : 'error'}`}>
                      <span className="label">Production Status:</span>
                      <span className="value">{productionRequirements.can_produce ? 'âœ… CAN PRODUCE' : 'âŒ INSUFFICIENT STOCK'}</span>
                    </div>
                  </div>
                  
                  {productionRequirements.shortages && productionRequirements.shortages.length > 0 && (
                    <div className="shortages-section">
                      <h5>âš ï¸ Stock Shortages Detected</h5>
                      <table className="stock-table">
                        <thead>
                          <tr>
                            <th>Raw Material</th>
                            <th>Required</th>
                            <th>Available</th>
                            <th>Shortage</th>
                          </tr>
                        </thead>
                        <tbody>
                          {productionRequirements.shortages.map((shortage, idx) => (
                            <tr key={idx} className="shortage-row">
                              <td>{shortage.raw_material_name}</td>
                              <td>{shortage.required}</td>
                              <td>{shortage.available}</td>
                              <td className="text-danger"><strong>{shortage.shortage}</strong></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  
                  <table className="stock-table">
                    <thead>
                      <tr>
                        <th>Raw Material</th>
                        <th>SKU</th>
                        <th>Qty per Unit</th>
                        <th>Required Qty</th>
                        <th>Unit</th>
                        <th>Unit Cost</th>
                        <th>Line Cost</th>
                        <th>Available Stock</th>
                        <th>Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {productionRequirements.requirements.map((req, idx) => (
                        <tr key={idx} className={req.sufficient_stock === false ? 'low-stock-row' : ''}>
                          <td>{req.raw_material_name}</td>
                          <td>{req.raw_material_sku}</td>
                          <td>{req.qty_per_unit}</td>
                          <td><strong>{req.required_quantity}</strong></td>
                          <td>{req.unit}</td>
                          <td>{formatCurrency(req.unit_cost)}</td>
                          <td>{formatCurrency(req.line_cost)}</td>
                          <td>{req.available_stock !== null ? req.available_stock : 'N/A'}</td>
                          <td>
                            {req.sufficient_stock === true && <span className="status-badge ok-stock">âœ… OK</span>}
                            {req.sufficient_stock === false && <span className="status-badge low-stock">âŒ INSUFFICIENT</span>}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  
                  {productionRequirements.can_produce && (
                    <div className="approval-section">
                      <button 
                        className="btn btn-success btn-large"
                        onClick={approveProductionAndDeduct}
                      >
                        âœ… Approve Production & Deduct Raw Materials from Stock
                      </button>
                      <p className="approval-note">
                        âš ï¸ This will deduct the required raw materials from <strong>{(data.warehouses||[]).find(w => w.id === productionRequirements.warehouse_id)?.name}</strong> inventory and create stock movement records.
                      </p>
                    </div>
                  )}
                </div>
              )}
              
              {/* Stock Display Area for BOM viewing */}
              {stockDisplayArea && (
                <div className="stock-display-area" dangerouslySetInnerHTML={{ __html: stockDisplayArea }}></div>
              )}
            </div>

            <div className="table-container">
              <h3>Production Orders</h3>
              <table className="data-table">
                <thead><tr><th>Order</th><th>Product</th><th>Planned Qty</th><th>Status</th><th>Actions</th></tr></thead>
                <tbody>
                  {(data.production||[]).map(order => (
                    <tr key={order.id}>
                      <td>{order.order_number || order.id}</td>
                      <td>{order.product_name || order.product_id}</td>
                      <td>{order.quantity_planned || order.quantity_to_produce}</td>
                      <td><span className={`status ${order.status}`}>{order.status}</span></td>
                      <td className="actions"><button onClick={() => openForm('productionOrder', order)} className="btn-edit">âœï¸</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Sales */}
        {activeModule === 'sales' && (
          <div className="module-content">
            <div className="module-header">
              <div className="module-header-left">
                <img src="/company-logo.png" alt="ASTRO-ASIX" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
                <h2>ðŸ’° Sales Orders</h2>
              </div>
              <button onClick={() => openForm('salesOrder')} className="btn btn-primary">âž• New Sales Order</button>
            </div>
            <div className="table-container">
              <table className="data-table">
                <thead><tr><th>Order</th><th>Customer</th><th>Total</th><th>Status</th><th>Payment</th><th>Actions</th></tr></thead>
                <tbody>
                  {(data.sales||[]).map(order => (
                    <tr key={order.id}>
                      <td>#{order.id}</td>
                      <td>{order.customer_name || order.customer_id}</td>
                      <td>{formatCurrency(order.total_amount)}</td>
                      <td><span className={`status ${order.status}`}>{order.status}</span></td>
                      <td><span className={`status ${order.payment_status}`}>{order.payment_status || 'pending'}</span></td>
                      <td className="actions">
                        <button onClick={() => generateInvoice(order.id)} className="btn-invoice">ðŸ“„ Invoice</button>
                        <button onClick={() => processOrder(order.id)} className="btn-paid">âœ… Process</button>
                        <button onClick={() => openForm('salesOrder', order)} className="btn-edit">âœï¸</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Stock Management */}
        {activeModule === 'stockManagement' && (
          <div className="module-content">
            <div className="module-header">
              <div className="module-header-left">
                <img src="/company-logo.png" alt="ASTRO-ASIX" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
                <h2>ðŸ“¦ Stock Management</h2>
              </div>
            </div>

            {/* Stock Management Action Buttons */}
            <div className="stock-actions-grid">
              <button className="action-btn product-intake" onClick={() => setShowForm('productStockIntake')}>
                ðŸ“¥ Product Stock Intake
              </button>
              <button className="action-btn raw-material-intake" onClick={() => setShowForm('rawMaterialStockIntake')}>
                ðŸ§ª Raw Material Intake
              </button>
              <button className="action-btn warehouse" onClick={() => openForm('warehouse')}>
                ðŸ¢ Create New Warehouse
              </button>
              <button className="action-btn view-levels" onClick={() => viewProductStockLevels(false)}>
                ðŸ“Š Product Stock Levels
              </button>
              <button className="action-btn view-levels" onClick={() => viewRawMaterialStockLevels(false)}>
                ðŸ“Š Raw Material Levels
              </button>
              <button className="action-btn low-stock" onClick={() => viewProductStockLevels(true)}>
                âš ï¸ Low Stock Products
              </button>
              <button className="action-btn low-stock" onClick={() => viewRawMaterialStockLevels(true)}>
                âš ï¸ Low Stock Raw Materials
              </button>
              <button className="action-btn damaged" onClick={() => setShowForm('damagedProduct')}>
                ðŸ”´ Record Damaged Product
              </button>
              <button className="action-btn damaged" onClick={() => setShowForm('damagedRawMaterial')}>
                ðŸ”´ Damaged Raw Material
              </button>
              <button className="action-btn returned" onClick={() => setShowForm('returnedProduct')}>
                â†©ï¸ Record Product Return
              </button>
              <button className="action-btn analysis" onClick={() => viewStockAnalysis()}>
                ðŸ“ˆ Stock Analysis & Dashboard
              </button>
            </div>

            {/* Stock Display Area */}
            {stockDisplayArea && (
              <div className="stock-display-area" dangerouslySetInnerHTML={{ __html: stockDisplayArea }}></div>
            )}
          </div>
        )}

        {/* Reports */}
        {activeModule === 'reports' && (
          <div className="module-content">
            <div className="module-header">
              <div className="module-header-left">
                <img src="/company-logo.png" alt="ASTRO-ASIX" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
                <h2>ðŸ“ˆ Reports</h2>
              </div>
            </div>
            <div className="reports-grid">
              <div className="report-card"><h3>Payment Status</h3><div className="report-stats"><div className="stat"><span className="label">Paid:</span><span className="value">{(data.sales||[]).filter(s=>s.payment_status==='paid').length}</span></div><div className="stat"><span className="label">Unpaid:</span><span className="value text-danger">{(data.sales||[]).filter(s=>s.payment_status==='unpaid').length}</span></div></div></div>
            </div>
          </div>
        )}

        {/* Settings */}
        {activeModule === 'settings' && (
          <div className="module-content">
            <div className="module-header">
              <div className="module-header-left">
                <img src="/company-logo.png" alt="ASTRO-ASIX" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
                <h2>âš™ï¸ Settings</h2>
              </div>
            </div>
            <div className="settings-card"><h3>Company</h3><div className="form-group"><label>Company Name</label><input type="text" defaultValue="ASTRO-ASIX"/></div></div>
          </div>
        )}
      </main>

      {/* Universal Modal */}
      {showForm && (
        <div className="modal-overlay">
          <div className="modal">
            <div className="modal-header">
              <div className="modal-header-left"><img src="/company-logo.png" alt="ASTRO-ASIX" className="modal-logo" onError={(e) => { e.target.style.display = 'none'; }}/><h3>{editingItem ? 'Edit' : 'Add'} {showForm.replace(/([A-Z])/g,' $1').trim()}</h3></div>
              <button className="modal-close" onClick={() => setShowForm('')}>âœ•</button>
            </div>
            <div className="modal-content">
              <img src="/company-logo.png" alt="ASTRO-ASIX" className="form-logo" onError={(e) => { e.target.style.display = 'none'; }} />
              {/* Staff */}
              {showForm === 'staff' && (
                <form onSubmit={(e)=>{e.preventDefault(); saveItem('staff','/api/staff/staffs');}}>
                  <div className="form-row">
                    <div className="form-group"><label>First Name *</label><input value={forms.staff.first_name} onChange={(e)=>handleFormChange('staff','first_name',e.target.value)} required/></div>
                    <div className="form-group"><label>Last Name *</label><input value={forms.staff.last_name} onChange={(e)=>handleFormChange('staff','last_name',e.target.value)} required/></div>
                    <div className="form-group"><label>Phone Number</label><input type="tel" value={forms.staff.phone} onChange={(e)=>handleFormChange('staff','phone',e.target.value)}/></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Date of Birth</label><input type="date" value={forms.staff.date_of_birth} onChange={(e)=>handleFormChange('staff','date_of_birth',e.target.value)}/></div>
                    <div className="form-group"><label>Position</label><input value={forms.staff.position} onChange={(e)=>handleFormChange('staff','position',e.target.value)}/></div>
                    <div className="form-group"><label>Hire Date</label><input type="date" value={forms.staff.hire_date} onChange={(e)=>handleFormChange('staff','hire_date',e.target.value)}/></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Payment Mode *</label><select value={forms.staff.payment_mode} onChange={(e)=>handleFormChange('staff','payment_mode',e.target.value)} required><option value="">Select payment mode</option><option value="monthly">Monthly</option><option value="hourly">Hour Worked (From Timed Attendance)</option></select></div>
                    {forms.staff.payment_mode === 'monthly' && (
                      <div className="form-group"><label>Monthly Salary (â‚¦) *</label><input type="number" step="0.01" value={forms.staff.monthly_salary} onChange={(e)=>handleFormChange('staff','monthly_salary',e.target.value)} required placeholder="e.g., 150000" /></div>
                    )}
                    {forms.staff.payment_mode === 'hourly' && (
                      <div className="form-group"><label>Hourly Rate (â‚¦) *</label><input type="number" step="0.01" value={forms.staff.hourly_rate} onChange={(e)=>handleFormChange('staff','hourly_rate',e.target.value)} required placeholder="e.g., 2500" /></div>
                    )}
                    <div className="form-group"><label>Bank Currency</label><input value={forms.staff.bank_currency} onChange={(e)=>handleFormChange('staff','bank_currency',e.target.value)} placeholder="NGN"/></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Bank Name</label><input value={forms.staff.bank_name} onChange={(e)=>handleFormChange('staff','bank_name',e.target.value)}/></div>
                    <div className="form-group"><label>Account Number</label><input value={forms.staff.bank_account_number} onChange={(e)=>handleFormChange('staff','bank_account_number',e.target.value)}/></div>
                    <div className="form-group"><label>Account Name</label><input value={forms.staff.bank_account_name} onChange={(e)=>handleFormChange('staff','bank_account_name',e.target.value)}/></div>
                  </div>
                  <div className="info-box">â„¹ï¸ Employee ID (BSM####) and Clock PIN will be auto-generated</div>
                  <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':(editingItem?'Update':'Add')} Staff</button></div>
                </form>
              )}

              {/* Product */}
              {showForm === 'product' && (
                <form onSubmit={(e)=>{e.preventDefault(); saveItem('product','/api/products/');}}>
                  <div className="form-row">
                    <div className="form-group"><label>Product Code/SKU *</label><input value={forms.product.sku} onChange={(e)=>handleFormChange('product','sku',e.target.value)} required/></div>
                    <div className="form-group"><label>Product Name *</label><input value={forms.product.name} onChange={(e)=>handleFormChange('product','name',e.target.value)} required/></div>
                    <div className="form-group"><label>Manufacturer</label><input value={forms.product.manufacturer} onChange={(e)=>handleFormChange('product','manufacturer',e.target.value)}/></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Unit of Measure (UoM)</label><input value={forms.product.unit} onChange={(e)=>handleFormChange('product','unit',e.target.value)} placeholder="each"/></div>
                    <div className="form-group"><label>Reorder Level</label><input type="number" step="0.01" value={forms.product.reorder_level} onChange={(e)=>handleFormChange('product','reorder_level',e.target.value)}/></div>
                    <div className="form-group"><label>Minimum Order Quantity (MOQ)</label><input type="number" step="0.01" value={forms.product.minimum_order_quantity} onChange={(e)=>handleFormChange('product','minimum_order_quantity',e.target.value)}/></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Cost Price (â‚¦)</label><input type="number" step="0.01" value={forms.product.cost_price} onChange={(e)=>handleFormChange('product','cost_price',e.target.value)}/></div>
                    <div className="form-group"><label>Retail Price (â‚¦)</label><input type="number" step="0.01" value={forms.product.retail_price} onChange={(e)=>handleFormChange('product','retail_price',e.target.value)}/></div>
                    <div className="form-group"><label>Wholesale Price (â‚¦)</label><input type="number" step="0.01" value={forms.product.wholesale_price} onChange={(e)=>handleFormChange('product','wholesale_price',e.target.value)}/></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Selling Price (â‚¦)</label><input type="number" step="0.01" value={forms.product.selling_price} onChange={(e)=>handleFormChange('product','selling_price',e.target.value)} placeholder="General selling price"/></div>
                    <div className="form-group"><label>Lead Time (Days)</label><input type="number" value={forms.product.lead_time_days} onChange={(e)=>handleFormChange('product','lead_time_days',e.target.value)}/></div>
                    <div className="form-group"></div>
                  </div>
                  <div className="form-group"><label>Description</label><textarea rows="3" value={forms.product.description} onChange={(e)=>handleFormChange('product','description',e.target.value)}/></div>
                  <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':(editingItem?'Update':'Add')} Product</button></div>
                </form>
              )}

              {/* Raw Material */}
              {showForm === 'rawMaterial' && (
                <form onSubmit={(e)=>{e.preventDefault(); saveItem('rawMaterial','/api/raw-materials/');}}>
                  <div className="form-row">
                    <div className="form-group"><label>Raw Material Name *</label><input value={forms.rawMaterial.name} onChange={(e)=>handleFormChange('rawMaterial','name',e.target.value)} required placeholder="e.g., Cotton Gauze"/></div>
                    <div className="form-group"><label>Raw Material Code / SKU *</label><input value={forms.rawMaterial.sku} onChange={(e)=>handleFormChange('rawMaterial','sku',e.target.value)} required placeholder="e.g., RM-001"/></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Manufacturer / Supplier</label><input value={forms.rawMaterial.manufacturer} onChange={(e)=>handleFormChange('rawMaterial','manufacturer',e.target.value)} placeholder="e.g., ABC Medical Supplies"/></div>
                    <div className="form-group"><label>Unit of Measure (UoM) *</label><select value={forms.rawMaterial.unit} onChange={(e)=>handleFormChange('rawMaterial','unit',e.target.value)} required><option value="kg">Kilogram (kg)</option><option value="g">Gram (g)</option><option value="L">Liter (L)</option><option value="mL">Milliliter (mL)</option><option value="m">Meter (m)</option><option value="cm">Centimeter (cm)</option><option value="pcs">Pieces (pcs)</option><option value="box">Box</option><option value="roll">Roll</option><option value="pack">Pack</option></select></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Reorder Level *</label><input type="number" step="0.01" value={forms.rawMaterial.reorder_level} onChange={(e)=>handleFormChange('rawMaterial','reorder_level',e.target.value)} required placeholder="Minimum stock level"/></div>
                    <div className="form-group"><label>Cost Price (â‚¦) *</label><input type="number" step="0.01" value={forms.rawMaterial.unit_cost} onChange={(e)=>handleFormChange('rawMaterial','unit_cost',e.target.value)} required placeholder="0.00"/></div>
                  </div>
                  <div className="form-group"><label>Date Added</label><input type="date" value={forms.rawMaterial.date_added} onChange={(e)=>handleFormChange('rawMaterial','date_added',e.target.value)} defaultValue={new Date().toISOString().split('T')[0]}/></div>
                  <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':'Add Raw Material'}</button></div>
                </form>
              )}

              {/* Stock Intake (Product) */}
              {showForm === 'stockIntake' && (
                <form onSubmit={async (e)=>{
                  e.preventDefault();
                  try {
                    setLoading(true);
                    const payload = { 
                      product_id: forms.stockIntake.product_id, 
                      warehouse_id: forms.stockIntake.warehouse_id, 
                      quantity: forms.stockIntake.quantity, 
                      unit_cost: forms.stockIntake.unit_cost || null, 
                      supplier: forms.stockIntake.supplier || null,
                      batch_number: forms.stockIntake.batch_number || null,
                      expiry_date: forms.stockIntake.expiry_date || null,
                      notes: forms.stockIntake.notes || null 
                    };
                    const res = await fetch('/api/stock/intake/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
                    if (!res.ok) throw new Error((await res.json()).detail || 'Failed');
                    notify('Stock intake recorded','success'); setShowForm(''); fetchAllData();
                  } catch(e){ notify(`Error: ${e.message}`,'error'); } finally { setLoading(false); }
                }}>
                  <div className="form-row">
                    <div className="form-group"><label>Product *</label><select value={forms.stockIntake.product_id} onChange={(e)=>handleFormChange('stockIntake','product_id',e.target.value)} required><option value="">Select product</option>{(data.products||[]).map(p=>(<option key={p.id} value={p.id}>{p.name}</option>))}</select></div>
                    <div className="form-group"><label>Warehouse *</label><select value={forms.stockIntake.warehouse_id} onChange={(e)=>handleFormChange('stockIntake','warehouse_id',e.target.value)} required><option value="">Select warehouse</option>{(data.warehouses||[]).map(w=>(<option key={w.id} value={w.id}>{w.name}</option>))}</select></div>
                    <div className="form-group"><label>Quantity *</label><input type="number" value={forms.stockIntake.quantity} onChange={(e)=>handleFormChange('stockIntake','quantity',e.target.value)} required/></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Unit Cost</label><input type="number" step="0.01" value={forms.stockIntake.unit_cost} onChange={(e)=>handleFormChange('stockIntake','unit_cost',e.target.value)}/></div>
                    <div className="form-group"><label>Supplier</label><input value={forms.stockIntake.supplier} onChange={(e)=>handleFormChange('stockIntake','supplier',e.target.value)}/></div>
                    <div className="form-group"><label>Batch Number</label><input value={forms.stockIntake.batch_number} onChange={(e)=>handleFormChange('stockIntake','batch_number',e.target.value)}/></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Expiry Date</label><input type="date" value={forms.stockIntake.expiry_date} onChange={(e)=>handleFormChange('stockIntake','expiry_date',e.target.value)}/></div>
                    <div className="form-group"><label>Notes</label><input value={forms.stockIntake.notes} onChange={(e)=>handleFormChange('stockIntake','notes',e.target.value)}/></div>
                  </div>
                  <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':'Record Intake'}</button></div>
                </form>
              )}

              {/* Raw Material Intake */}
              {showForm === 'rawMaterialIntake' && (
                <form onSubmit={async (e)=>{
                  e.preventDefault();
                  try {
                    setLoading(true);
                    const payload = { 
                      raw_material_id: forms.rawMaterialIntake.raw_material_id, 
                      warehouse_id: forms.rawMaterialIntake.warehouse_id, 
                      quantity: forms.rawMaterialIntake.quantity, 
                      unit_cost: forms.rawMaterialIntake.unit_cost || null, 
                      supplier: forms.rawMaterialIntake.supplier || null,
                      batch_number: forms.rawMaterialIntake.batch_number || null,
                      expiry_date: forms.rawMaterialIntake.expiry_date || null,
                      notes: forms.rawMaterialIntake.notes || null 
                    };
                    const res = await fetch('/api/stock/intake/raw-material/', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
                    if (!res.ok) throw new Error((await res.json()).detail || 'Failed');
                    notify('Raw material intake recorded','success'); setShowForm(''); fetchAllData();
                  } catch(e){ notify(`Error: ${e.message}`,'error'); } finally { setLoading(false); }
                }}>
                  <div className="form-row">
                    <div className="form-group"><label>Raw Material *</label><select value={forms.rawMaterialIntake.raw_material_id} onChange={(e)=>handleFormChange('rawMaterialIntake','raw_material_id',e.target.value)} required><option value="">Select raw material</option>{(data.rawMaterials||[]).map(r=>(<option key={r.id} value={r.id}>{r.name}</option>))}</select></div>
                    <div className="form-group"><label>Warehouse *</label><select value={forms.rawMaterialIntake.warehouse_id} onChange={(e)=>handleFormChange('rawMaterialIntake','warehouse_id',e.target.value)} required><option value="">Select warehouse</option>{(data.warehouses||[]).map(w=>(<option key={w.id} value={w.id}>{w.name}</option>))}</select></div>
                    <div className="form-group"><label>Quantity *</label><input type="number" value={forms.rawMaterialIntake.quantity} onChange={(e)=>handleFormChange('rawMaterialIntake','quantity',e.target.value)} required/></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Unit Cost</label><input type="number" step="0.01" value={forms.rawMaterialIntake.unit_cost} onChange={(e)=>handleFormChange('rawMaterialIntake','unit_cost',e.target.value)}/></div>
                    <div className="form-group"><label>Supplier</label><input value={forms.rawMaterialIntake.supplier} onChange={(e)=>handleFormChange('rawMaterialIntake','supplier',e.target.value)}/></div>
                    <div className="form-group"><label>Batch Number</label><input value={forms.rawMaterialIntake.batch_number} onChange={(e)=>handleFormChange('rawMaterialIntake','batch_number',e.target.value)}/></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Expiry Date</label><input type="date" value={forms.rawMaterialIntake.expiry_date} onChange={(e)=>handleFormChange('rawMaterialIntake','expiry_date',e.target.value)}/></div>
                    <div className="form-group"><label>Notes</label><input value={forms.rawMaterialIntake.notes} onChange={(e)=>handleFormChange('rawMaterialIntake','notes',e.target.value)}/></div>
                  </div>
                  <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':'Record Intake'}</button></div>
                </form>
              )}

              {/* Warehouse */}
              {showForm === 'warehouse' && (
                <form onSubmit={(e)=>{e.preventDefault(); saveItem('warehouse','/api/warehouses/');}}>
                  <div className="form-row">
                    <div className="form-group"><label>Code *</label><input value={forms.warehouse.code} onChange={(e)=>handleFormChange('warehouse','code',e.target.value)} required placeholder="e.g., WH-001"/></div>
                    <div className="form-group"><label>Name *</label><input value={forms.warehouse.name} onChange={(e)=>handleFormChange('warehouse','name',e.target.value)} required placeholder="e.g., Main Warehouse"/></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Location</label><input value={forms.warehouse.location} onChange={(e)=>handleFormChange('warehouse','location',e.target.value)} placeholder="e.g., Lagos, Nigeria"/></div>
                    <div className="form-group">
                      <label>Warehouse Manager</label>
                      <select value={forms.warehouse.manager_id} onChange={(e)=>handleFormChange('warehouse','manager_id',e.target.value)}>
                        <option value="">No Manager Assigned</option>
                        {(data.staff||[]).map(s=>(<option key={s.id} value={s.id}>{s.first_name} {s.last_name} - {s.position || 'Staff'}</option>))}
                      </select>
                    </div>
                  </div>
                  <div className="info-box">
                    â„¹ï¸ Warehouse manager can be assigned to oversee operations. This can be edited later by admins.
                  </div>
                  <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':(editingItem?'Update':'Add')+' Warehouse'}</button></div>
                </form>
              )}

              {/* Customer */}
              {showForm === 'customer' && (
                <form onSubmit={(e)=>{e.preventDefault(); saveItem('customer','/api/sales/customers');}}>
                  <div className="form-row">
                    <div className="form-group">
                      <label>Customer Code *</label>
                      <input 
                        value={forms.customer.customer_code} 
                        onChange={(e)=>handleFormChange('customer','customer_code',e.target.value)} 
                        required 
                        placeholder="e.g., CUST-001"
                        maxLength="32"
                      />
                    </div>
                    <div className="form-group">
                      <label>Customer Name *</label>
                      <input 
                        value={forms.customer.name} 
                        onChange={(e)=>handleFormChange('customer','name',e.target.value)} 
                        required 
                        placeholder="Company or Individual Name"
                        maxLength="255"
                      />
                    </div>
                  </div>
                  <div className="form-row">
                    <div className="form-group">
                      <label>Email</label>
                      <input 
                        type="email"
                        value={forms.customer.email} 
                        onChange={(e)=>handleFormChange('customer','email',e.target.value)} 
                        placeholder="customer@example.com"
                        maxLength="255"
                      />
                    </div>
                    <div className="form-group">
                      <label>Phone Number</label>
                      <input 
                        type="tel"
                        value={forms.customer.phone} 
                        onChange={(e)=>handleFormChange('customer','phone',e.target.value)} 
                        placeholder="08012345678"
                        maxLength="50"
                      />
                    </div>
                  </div>
                  <div className="form-row">
                    <div className="form-group">
                      <label>Address</label>
                      <textarea 
                        rows="3"
                        value={forms.customer.address} 
                        onChange={(e)=>handleFormChange('customer','address',e.target.value)} 
                        placeholder="Full business or residential address"
                      />
                    </div>
                  </div>
                  <div className="form-row">
                    <div className="form-group">
                      <label>Credit Limit (â‚¦)</label>
                      <input 
                        type="number"
                        step="0.01"
                        min="0"
                        value={forms.customer.credit_limit} 
                        onChange={(e)=>handleFormChange('customer','credit_limit',e.target.value)} 
                        placeholder="0.00"
                      />
                      <small style={{color: 'var(--gray-600)', fontSize: '0.85rem', marginTop: '0.25rem', display: 'block'}}>
                        Maximum amount customer can purchase on credit
                      </small>
                    </div>
                  </div>
                  <div className="info-box">
                    â„¹ï¸ Customer code should be unique. Email and phone help with communication and reporting.
                  </div>
                  <div className="modal-actions">
                    <button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button>
                    <button type="submit" className="btn btn-primary" disabled={loading}>
                      {loading ? 'Saving...' : (editingItem ? 'Update' : 'Add')} Customer
                    </button>
                  </div>
                </form>
              )}

              {/* Payroll */}
              {showForm === 'payroll' && (
                <form onSubmit={submitPayroll}>
                  <div className="form-row">
                    <div className="form-group"><label>Staff *</label><select value={forms.payroll.staff_id} onChange={(e)=>handleFormChange('payroll','staff_id',e.target.value)} required><option value="">Select staff</option>{(data.staff||[]).map(s=>(<option key={s.id} value={s.id}>{s.first_name} {s.last_name}</option>))}</select></div>
                    <div className="form-group"><label>Period Start *</label><input type="date" value={forms.payroll.pay_period_start} onChange={(e)=>handleFormChange('payroll','pay_period_start',e.target.value)} required/></div>
                    <div className="form-group"><label>Period End *</label><input type="date" value={forms.payroll.pay_period_end} onChange={(e)=>handleFormChange('payroll','pay_period_end',e.target.value)} required/></div>
                  </div>
                  <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Processing...':'Process Payroll'}</button></div>
                </form>
              )}

              {/* Sales Order */}
              {showForm === 'salesOrder' && (
                <form onSubmit={submitSalesOrder}>
                  <div className="form-row">
                    <div className="form-group"><label>Customer *</label><select value={forms.salesOrder.customer_id} onChange={(e)=>handleFormChange('salesOrder','customer_id',e.target.value)} required><option value="">Select customer</option>{(data.customers||[]).map(c=>(<option key={c.id} value={c.id}>{c.name}</option>))}</select></div>
                    <div className="form-group"><button type="button" className="btn btn-secondary" onClick={()=>openForm('customer')} style={{marginTop:'1.5rem'}}>âž• New Customer</button></div>
                    <div className="form-group"><label>Required Date</label><input type="date" value={forms.salesOrder.required_date} onChange={(e)=>handleFormChange('salesOrder','required_date',e.target.value)}/></div>
                  </div>
                  <div className="form-group"><label>Notes</label><textarea rows="2" value={forms.salesOrder.notes} onChange={(e)=>handleFormChange('salesOrder','notes',e.target.value)}/></div>

                  <div className="lines-section">
                    <div className="lines-header">
                      <h4>Order Lines</h4>
                      <button type="button" className="btn btn-secondary" onClick={addSalesLine}>âž• Add Line</button>
                    </div>
                    {(forms.salesOrder.lines||[]).map((line, idx) => (
                      <div key={idx} className="form-row line-row">
                        <div className="form-group"><label>Product *</label><select value={line.product_id} onChange={(e)=>updateSalesLine(idx,'product_id',e.target.value)} required><option value="">Select product</option>{(data.products||[]).map(p=>(<option key={p.id} value={p.id}>{p.name}</option>))}</select></div>
                        <div className="form-group"><label>Qty *</label><input type="number" value={line.quantity} onChange={(e)=>updateSalesLine(idx,'quantity',e.target.value)} required/></div>
                        <div className="form-group"><label>Unit Price *</label><input type="number" step="0.01" value={line.unit_price} onChange={(e)=>updateSalesLine(idx,'unit_price',e.target.value)} required/></div>
                        <div className="form-group"><button type="button" className="btn btn-danger" onClick={()=>removeSalesLine(idx)}>ðŸ—‘ï¸</button></div>
                      </div>
                    ))}
                  </div>

                  <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':'Create Order'}</button></div>
                </form>
              )}

              {/* Product Stock Intake */}
              {showForm === 'productStockIntake' && (
                <form onSubmit={submitProductStockIntake}>
                  <div className="form-row">
                    <div className="form-group"><label>Warehouse *</label><select value={forms.stockIntake.warehouse_id} onChange={(e)=>handleFormChange('stockIntake','warehouse_id',e.target.value)} required><option value="">Select warehouse</option>{(data.warehouses||[]).map(w=>(<option key={w.id} value={w.id}>{w.name}</option>))}</select></div>
                    <div className="form-group"><label>Product *</label><select value={forms.stockIntake.product_id} onChange={(e)=>handleFormChange('stockIntake','product_id',e.target.value)} required><option value="">Select product</option>{(data.products||[]).map(p=>(<option key={p.id} value={p.id}>{p.name} - {p.sku}</option>))}</select></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Quantity *</label><input type="number" step="0.01" value={forms.stockIntake.quantity} onChange={(e)=>handleFormChange('stockIntake','quantity',e.target.value)} required/></div>
                    <div className="form-group"><label>Unit Cost (â‚¦) *</label><input type="number" step="0.01" value={forms.stockIntake.unit_cost} onChange={(e)=>handleFormChange('stockIntake','unit_cost',e.target.value)} required/></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Supplier</label><input value={forms.stockIntake.supplier} onChange={(e)=>handleFormChange('stockIntake','supplier',e.target.value)}/></div>
                    <div className="form-group"><label>Batch Number</label><input value={forms.stockIntake.batch_number} onChange={(e)=>handleFormChange('stockIntake','batch_number',e.target.value)}/></div>
                  </div>
                  <div className="form-group"><label>Notes</label><textarea rows="2" value={forms.stockIntake.notes} onChange={(e)=>handleFormChange('stockIntake','notes',e.target.value)}/></div>
                  <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Recording...':'Record Stock Intake'}</button></div>
                </form>
              )}

              {/* Raw Material Stock Intake */}
              {showForm === 'rawMaterialStockIntake' && (
                <form onSubmit={submitRawMaterialStockIntake}>
                  <div className="form-row">
                    <div className="form-group"><label>Warehouse *</label><select value={forms.rawMaterialIntake.warehouse_id} onChange={(e)=>handleFormChange('rawMaterialIntake','warehouse_id',e.target.value)} required><option value="">Select warehouse</option>{(data.warehouses||[]).map(w=>(<option key={w.id} value={w.id}>{w.name}</option>))}</select></div>
                    <div className="form-group"><label>Raw Material *</label><select value={forms.rawMaterialIntake.raw_material_id} onChange={(e)=>handleFormChange('rawMaterialIntake','raw_material_id',e.target.value)} required><option value="">Select raw material</option>{(data.rawMaterials||[]).map(r=>(<option key={r.id} value={r.id}>{r.name} - {r.sku}</option>))}</select></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Quantity *</label><input type="number" step="0.01" value={forms.rawMaterialIntake.quantity} onChange={(e)=>handleFormChange('rawMaterialIntake','quantity',e.target.value)} required/></div>
                    <div className="form-group"><label>Unit Cost (â‚¦) *</label><input type="number" step="0.01" value={forms.rawMaterialIntake.unit_cost} onChange={(e)=>handleFormChange('rawMaterialIntake','unit_cost',e.target.value)} required/></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Supplier</label><input value={forms.rawMaterialIntake.supplier} onChange={(e)=>handleFormChange('rawMaterialIntake','supplier',e.target.value)}/></div>
                    <div className="form-group"><label>Batch Number</label><input value={forms.rawMaterialIntake.batch_number} onChange={(e)=>handleFormChange('rawMaterialIntake','batch_number',e.target.value)}/></div>
                  </div>
                  <div className="form-group"><label>Notes</label><textarea rows="2" value={forms.rawMaterialIntake.notes} onChange={(e)=>handleFormChange('rawMaterialIntake','notes',e.target.value)}/></div>
                  <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Recording...':'Record Raw Material Intake'}</button></div>
                </form>
              )}

              {/* Damaged Product */}
              {showForm === 'damagedProduct' && (
                <form onSubmit={submitDamagedProduct}>
                  <div className="form-row">
                    <div className="form-group"><label>Warehouse *</label><select value={forms.damagedProduct.warehouse_id} onChange={(e)=>handleFormChange('damagedProduct','warehouse_id',e.target.value)} required><option value="">Select warehouse</option>{(data.warehouses||[]).map(w=>(<option key={w.id} value={w.id}>{w.name}</option>))}</select></div>
                    <div className="form-group"><label>Product *</label><select value={forms.damagedProduct.product_id} onChange={(e)=>handleFormChange('damagedProduct','product_id',e.target.value)} required><option value="">Select product</option>{(data.products||[]).map(p=>(<option key={p.id} value={p.id}>{p.name} - {p.sku}</option>))}</select></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Quantity *</label><input type="number" step="0.01" value={forms.damagedProduct.quantity} onChange={(e)=>handleFormChange('damagedProduct','quantity',e.target.value)} required/></div>
                    <div className="form-group"><label>Damage Type *</label><select value={forms.damagedProduct.damage_type} onChange={(e)=>handleFormChange('damagedProduct','damage_type',e.target.value)} required><option value="">Select type</option><option value="Physical Damage">Physical Damage</option><option value="Expired">Expired</option><option value="Defective">Defective</option><option value="Water Damage">Water Damage</option><option value="Other">Other</option></select></div>
                  </div>
                  <div className="form-group"><label>Damage Reason</label><textarea rows="2" value={forms.damagedProduct.damage_reason} onChange={(e)=>handleFormChange('damagedProduct','damage_reason',e.target.value)} placeholder="Describe the damage"/></div>
                  <div className="form-group"><label>Additional Notes</label><textarea rows="2" value={forms.damagedProduct.notes} onChange={(e)=>handleFormChange('damagedProduct','notes',e.target.value)}/></div>
                  <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Recording...':'Record Damaged Product'}</button></div>
                </form>
              )}

              {/* Damaged Raw Material */}
              {showForm === 'damagedRawMaterial' && (
                <form onSubmit={submitDamagedRawMaterial}>
                  <div className="form-row">
                    <div className="form-group"><label>Warehouse *</label><select value={forms.damagedRawMaterial.warehouse_id} onChange={(e)=>handleFormChange('damagedRawMaterial','warehouse_id',e.target.value)} required><option value="">Select warehouse</option>{(data.warehouses||[]).map(w=>(<option key={w.id} value={w.id}>{w.name}</option>))}</select></div>
                    <div className="form-group"><label>Raw Material *</label><select value={forms.damagedRawMaterial.raw_material_id} onChange={(e)=>handleFormChange('damagedRawMaterial','raw_material_id',e.target.value)} required><option value="">Select raw material</option>{(data.rawMaterials||[]).map(r=>(<option key={r.id} value={r.id}>{r.name} - {r.sku}</option>))}</select></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Quantity *</label><input type="number" step="0.01" value={forms.damagedRawMaterial.quantity} onChange={(e)=>handleFormChange('damagedRawMaterial','quantity',e.target.value)} required/></div>
                    <div className="form-group"><label>Damage Type *</label><select value={forms.damagedRawMaterial.damage_type} onChange={(e)=>handleFormChange('damagedRawMaterial','damage_type',e.target.value)} required><option value="">Select type</option><option value="Physical Damage">Physical Damage</option><option value="Expired">Expired</option><option value="Contaminated">Contaminated</option><option value="Spoiled">Spoiled</option><option value="Other">Other</option></select></div>
                  </div>
                  <div className="form-group"><label>Damage Reason</label><textarea rows="2" value={forms.damagedRawMaterial.damage_reason} onChange={(e)=>handleFormChange('damagedRawMaterial','damage_reason',e.target.value)} placeholder="Describe the damage"/></div>
                  <div className="form-group"><label>Additional Notes</label><textarea rows="2" value={forms.damagedRawMaterial.notes} onChange={(e)=>handleFormChange('damagedRawMaterial','notes',e.target.value)}/></div>
                  <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Recording...':'Record Damaged Raw Material'}</button></div>
                </form>
              )}

              {/* Returned Product */}
              {showForm === 'returnedProduct' && (
                <form onSubmit={submitReturnedProduct}>
                  <div className="form-row">
                    <div className="form-group"><label>Warehouse *</label><select value={forms.returnedProduct.warehouse_id} onChange={(e)=>handleFormChange('returnedProduct','warehouse_id',e.target.value)} required><option value="">Select warehouse</option>{(data.warehouses||[]).map(w=>(<option key={w.id} value={w.id}>{w.name}</option>))}</select></div>
                    <div className="form-group"><label>Product *</label><select value={forms.returnedProduct.product_id} onChange={(e)=>handleFormChange('returnedProduct','product_id',e.target.value)} required><option value="">Select product</option>{(data.products||[]).map(p=>(<option key={p.id} value={p.id}>{p.name} - {p.sku}</option>))}</select></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Quantity *</label><input type="number" step="0.01" value={forms.returnedProduct.quantity} onChange={(e)=>handleFormChange('returnedProduct','quantity',e.target.value)} required/></div>
                    <div className="form-group"><label>Return Condition *</label><select value={forms.returnedProduct.return_condition} onChange={(e)=>handleFormChange('returnedProduct','return_condition',e.target.value)} required><option value="good">Good (Resaleable)</option><option value="damaged">Damaged</option><option value="expired">Expired</option></select></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Customer Name</label><input value={forms.returnedProduct.customer_name} onChange={(e)=>handleFormChange('returnedProduct','customer_name',e.target.value)}/></div>
                    <div className="form-group"><label>Refund Amount (â‚¦)</label><input type="number" step="0.01" value={forms.returnedProduct.refund_amount} onChange={(e)=>handleFormChange('returnedProduct','refund_amount',e.target.value)}/></div>
                  </div>
                  <div className="form-group"><label>Return Reason *</label><textarea rows="2" value={forms.returnedProduct.return_reason} onChange={(e)=>handleFormChange('returnedProduct','return_reason',e.target.value)} placeholder="Why is the customer returning this?" required/></div>
                  <div className="form-group"><label>Additional Notes</label><textarea rows="2" value={forms.returnedProduct.notes} onChange={(e)=>handleFormChange('returnedProduct','notes',e.target.value)}/></div>
                  <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Recording...':'Record Product Return'}</button></div>
                </form>
              )}

              {/* Production Order */}
              {showForm === 'bom' && (
                <form onSubmit={(e) => { e.preventDefault(); saveBOM(); }}>
                  <div className="form-group">
                    <label>Product *</label>
                    <select 
                      value={forms.bom.product_id} 
                      onChange={(e) => handleFormChange('bom', 'product_id', e.target.value)}
                      required
                    >
                      <option value="">Select Product</option>
                      {(data.products||[]).map(p => (
                        <option key={p.id} value={p.id}>{p.name} - {p.sku}</option>
                      ))}
                    </select>
                  </div>
                  
                  <div className="bom-lines-section">
                    <h4>ðŸ“‹ Raw Material Lines</h4>
                    {bomLines.map((line) => (
                      <div key={line.id} className="bom-line">
                        <div className="form-row">
                          <div className="form-group" style={{flex: '2'}}>
                            <label>Raw Material *</label>
                            <select 
                              value={line.raw_material_id} 
                              onChange={(e) => updateBomLine(line.id, 'raw_material_id', e.target.value)}
                              required
                            >
                              <option value="">Select Raw Material</option>
                              {(data.rawMaterials||[]).map(rm => (
                                <option key={rm.id} value={rm.id}>{rm.name} - {rm.sku}</option>
                              ))}
                            </select>
                          </div>
                          <div className="form-group">
                            <label>Qty per Unit *</label>
                            <input 
                              type="number" 
                              step="0.01" 
                              value={line.qty_per_unit} 
                              onChange={(e) => updateBomLine(line.id, 'qty_per_unit', e.target.value)}
                              placeholder="0.00"
                              required
                            />
                          </div>
                          <div className="form-group">
                            <label>Unit *</label>
                            <select 
                              value={line.unit} 
                              onChange={(e) => updateBomLine(line.id, 'unit', e.target.value)}
                              required
                            >
                              <option value="kg">kg</option>
                              <option value="g">g</option>
                              <option value="L">L</option>
                              <option value="mL">mL</option>
                              <option value="m">m</option>
                              <option value="cm">cm</option>
                              <option value="pcs">pcs</option>
                              <option value="box">box</option>
                              <option value="roll">roll</option>
                              <option value="pack">pack</option>
                            </select>
                          </div>
                          <div className="form-group" style={{flex: '0'}}>
                            <label>&nbsp;</label>
                            <button 
                              type="button" 
                              className="btn btn-danger" 
                              onClick={() => removeBomLine(line.id)}
                              disabled={bomLines.length === 1}
                            >
                              ðŸ—‘ï¸
                            </button>
                          </div>
                        </div>
                      </div>
                    ))}
                    <button 
                      type="button" 
                      className="btn btn-secondary" 
                      onClick={addBomLine}
                      style={{marginTop: '10px'}}
                    >
                      âž• Add Raw Material Line
                    </button>
                  </div>
                  
                  <div className="modal-actions">
                    <button type="button" className="btn btn-secondary" onClick={() => setShowForm('')}>Cancel</button>
                    <button type="submit" className="btn btn-primary" disabled={loading}>
                      {loading ? 'Saving...' : 'ðŸ’¾ Save BOM'}
                    </button>
                  </div>
                </form>
              )}

              {showForm === 'productionOrder' && (
                <form onSubmit={submitProductionOrder}>
                  <div className="form-row">
                    <div className="form-group"><label>Product *</label><select value={forms.productionOrder.product_id} onChange={(e)=>handleFormChange('productionOrder','product_id',e.target.value)} required><option value="">Select product</option>{(data.products||[]).map(p=>(<option key={p.id} value={p.id}>{p.name}</option>))}</select></div>
                    <div className="form-group"><label>Quantity Planned *</label><input type="number" value={forms.productionOrder.quantity_planned} onChange={(e)=>handleFormChange('productionOrder','quantity_planned',e.target.value)} required/></div>
                  </div>
                  <div className="form-row">
                    <div className="form-group"><label>Start Date</label><input type="date" value={forms.productionOrder.scheduled_start_date} onChange={(e)=>handleFormChange('productionOrder','scheduled_start_date',e.target.value)}/></div>
                    <div className="form-group"><label>End Date</label><input type="date" value={forms.productionOrder.scheduled_end_date} onChange={(e)=>handleFormChange('productionOrder','scheduled_end_date',e.target.value)}/></div>
                    <div className="form-group"><label>Priority</label><input type="number" value={forms.productionOrder.priority} onChange={(e)=>handleFormChange('productionOrder','priority',e.target.value)}/></div>
                  </div>
                  <div className="form-group"><label>Notes</label><textarea rows="2" value={forms.productionOrder.notes} onChange={(e)=>handleFormChange('productionOrder','notes',e.target.value)}/></div>
                  <div className="modal-actions"><button type="button" className="btn btn-secondary" onClick={()=>setShowForm('')}>Cancel</button><button type="submit" className="btn btn-primary" disabled={loading}>{loading?'Saving...':'Create Production Order'}</button></div>
                </form>
              )}
            </div>
          </div>
        </div>
      )}

      {loading && (<div className="loading-overlay"><div className="loading-spinner">â³ Loading...</div></div>)}
      </div>
    </div>
  );

  // Attendance PIN-based clock in/out
  async function handlePinClock(action) {
    const pinInput = document.getElementById('clockPinInput');
    const pin = pinInput.value.trim();
    
    if (!pin || pin.length !== 4) {
      notify('Please enter a valid 4-digit PIN', 'error');
      return;
    }
    
    try {
      setLoading(true);
      const res = await fetch('/api/attendance/quick-attendance', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin, action, notes: '' })
      });
      
      const result = await res.json();
      
      if (result.success) {
        notify(`${result.message} - ${result.staff_name}`, 'success');
        pinInput.value = '';
        fetchData('attendance');
      } else {
        notify(result.message, 'error');
      }
    } catch (e) {
      notify(`Error: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }
  
  // View current attendance status
  async function viewAttendanceStatus() {
    try {
      setLoading(true);
      const res = await fetch('/api/attendance/status');
      if (!res.ok) throw new Error('Failed to fetch status');
      
      const statusList = await res.json();
      
      const displayArea = document.getElementById('attendanceDisplayArea');
      if (!displayArea) return;
      
      const clockedIn = statusList.filter(s => s.status === 'clocked_in');
      const clockedOut = statusList.filter(s => s.status === 'clocked_out');
      const notClockedIn = statusList.filter(s => s.status === 'not_clocked_in');
      
      displayArea.innerHTML = `
        <div class="attendance-status-container">
          <h3>ðŸ“Š Current Attendance Status</h3>
          
          <div class="status-summary">
            <div class="status-badge clocked-in">âœ… Clocked In: ${clockedIn.length}</div>
            <div class="status-badge clocked-out">â¹ï¸ Clocked Out: ${clockedOut.length}</div>
            <div class="status-badge not-clocked">âš ï¸ Not Clocked In: ${notClockedIn.length}</div>
          </div>
          
          <div class="status-section">
            <h4>âœ… Currently Clocked In</h4>
            ${clockedIn.length > 0 ? `
              <table class="data-table">
                <thead><tr><th>Employee ID</th><th>Name</th><th>Position</th><th>Clock In Time</th><th>Hours So Far</th></tr></thead>
                <tbody>
                  ${clockedIn.map(s => `
                    <tr>
                      <td>${s.employee_id}</td>
                      <td>${s.staff_name}</td>
                      <td>${s.position || 'N/A'}</td>
                      <td>${new Date(s.clock_in_time).toLocaleString()}</td>
                      <td>${s.hours_so_far.toFixed(2)} hrs</td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            ` : '<p class="no-data">No staff currently clocked in</p>'}
          </div>
          
          <div class="status-section">
            <h4>â¹ï¸ Clocked Out Today</h4>
            ${clockedOut.length > 0 ? `
              <table class="data-table">
                <thead><tr><th>Employee ID</th><th>Name</th><th>Position</th><th>Clock In</th><th>Clock Out</th><th>Hours Worked</th></tr></thead>
                <tbody>
                  ${clockedOut.map(s => `
                    <tr>
                      <td>${s.employee_id}</td>
                      <td>${s.staff_name}</td>
                      <td>${s.position || 'N/A'}</td>
                      <td>${new Date(s.clock_in_time).toLocaleTimeString()}</td>
                      <td>${new Date(s.clock_out_time).toLocaleTimeString()}</td>
                      <td>${(s.hours_worked || 0).toFixed(2)} hrs</td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            ` : '<p class="no-data">No staff have clocked out today</p>'}
          </div>
        </div>
      `;
    } catch (e) {
      notify(`Error fetching status: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }
  
  // View detailed attendance log with punctuality
  async function viewDetailedLog() {
    try {
      setLoading(true);
      const res = await fetch('/api/attendance/detailed-log');
      if (!res.ok) throw new Error('Failed to fetch detailed log');
      
      const logData = await res.json();
      
      const displayArea = document.getElementById('attendanceDisplayArea');
      if (!displayArea) return;
      
      const getPunctualityBadge = (status, minutes) => {
        if (status === 'early') return `<span class="punctuality-badge early">âœ… Early (${minutes} min)</span>`;
        if (status === 'on_time') return `<span class="punctuality-badge on-time">âœ… On Time (${minutes} min)</span>`;
        if (status === 'slightly_late') return `<span class="punctuality-badge slightly-late">âš ï¸ Slightly Late (${minutes} min)</span>`;
        return `<span class="punctuality-badge late">âŒ Late (${minutes} min)</span>`;
      };
      
      displayArea.innerHTML = `
        <div class="detailed-log-container">
          <h3>ðŸ“‹ Detailed Attendance Log (Last 30 Days)</h3>
          <p class="log-info">Showing attendance records with punctuality analysis. Standard start time: 9:00 AM</p>
          
          ${logData.length > 0 ? `
            <div class="table-container">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>Date</th>
                    <th>Employee ID</th>
                    <th>Name</th>
                    <th>Position</th>
                    <th>Clock In</th>
                    <th>Clock Out</th>
                    <th>Hours</th>
                    <th>Punctuality</th>
                  </tr>
                </thead>
                <tbody>
                  ${logData.map(log => `
                    <tr>
                      <td>${log.date}</td>
                      <td>${log.employee_id}</td>
                      <td>${log.staff_name}</td>
                      <td>${log.position || 'N/A'}</td>
                      <td>${log.clock_in}</td>
                      <td>${log.clock_out || 'In Progress'}</td>
                      <td>${(log.hours_worked || 0).toFixed(2)} hrs</td>
                      <td>${getPunctualityBadge(log.punctuality_status, log.punctuality_minutes)}</td>
                    </tr>
                  `).join('')}
                </tbody>
              </table>
            </div>
          ` : '<p class="no-data">No attendance records found for the last 30 days</p>'}
        </div>
      `;
    } catch (e) {
      notify(`Error fetching detailed log: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }
  
  // View best performing staff
  async function viewBestPerformers() {
    try {
      setLoading(true);
      const res = await fetch('/api/attendance/best-performers?limit=10');
      if (!res.ok) throw new Error('Failed to fetch best performers');
      
      const performers = await res.json();
      
      const displayArea = document.getElementById('attendanceDisplayArea');
      if (!displayArea) return;
      
      displayArea.innerHTML = `
        <div class="best-performers-container">
          <h3>ðŸ† Best Performing Staff (Last 30 Days)</h3>
          <p class="performers-info">Ranking based on punctuality (70%) and attendance regularity (30%)</p>
          
          ${performers.length > 0 ? `
            <div class="performers-grid">
              ${performers.map((staff, index) => `
                <div class="performer-card ${index < 3 ? 'top-performer' : ''}">
                  <div class="performer-rank">${index < 3 ? ['ðŸ¥‡', 'ðŸ¥ˆ', 'ðŸ¥‰'][index] : `#${index + 1}`}</div>
                  <div class="performer-info">
                    <h4>${staff.staff_name}</h4>
                    <p class="performer-id">${staff.employee_id} â€¢ ${staff.position || 'N/A'}</p>
                    <div class="performer-score">Performance Score: <strong>${staff.performance_score}%</strong></div>
                  </div>
                  <div class="performer-stats">
                    <div class="stat-row">
                      <span class="stat-label">ðŸ“… Days Attended:</span>
                      <span class="stat-value">${staff.total_days_attended}</span>
                    </div>
                    <div class="stat-row">
                      <span class="stat-label">â±ï¸ Total Hours:</span>
                      <span class="stat-value">${staff.total_hours_worked} hrs</span>
                    </div>
                    <div class="stat-row">
                      <span class="stat-label">ðŸ“Š Avg Hours/Day:</span>
                      <span class="stat-value">${staff.avg_hours_per_day} hrs</span>
                    </div>
                    <div class="stat-row">
                      <span class="stat-label">âœ… Early Arrivals:</span>
                      <span class="stat-value">${staff.early_arrivals}</span>
                    </div>
                    <div class="stat-row">
                      <span class="stat-label">â° On Time:</span>
                      <span class="stat-value">${staff.on_time_arrivals}</span>
                    </div>
                    <div class="stat-row">
                      <span class="stat-label">âš ï¸ Late Arrivals:</span>
                      <span class="stat-value">${staff.late_arrivals}</span>
                    </div>
                    <div class="stat-row">
                      <span class="stat-label">ðŸŽ¯ Punctuality:</span>
                      <span class="stat-value">${staff.punctuality_score}%</span>
                    </div>
                  </div>
                </div>
              `).join('')}
            </div>
          ` : '<p class="no-data">No performance data available for the last 30 days</p>'}
        </div>
      `;
    } catch (e) {
      notify(`Error fetching best performers: ${e.message}`, 'error');
    } finally {
      setLoading(false);
    }
  }
}

export default App;



