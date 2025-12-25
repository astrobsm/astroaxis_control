// Enhanced Raw Materials Module Section
// This replaces the basic raw materials module with comprehensive display

{/* Raw Materials */}
{activeModule === 'rawMaterials' && (
  <div className="module-content">
    <div className="module-header">
      <div className="module-header-left">
        <img src="/company-logo.png" alt="ASTRO-ASIX" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
        <h2>🧱 Raw Materials</h2>
      </div>
      <div className="module-actions">
        <button onClick={() => openForm('rawMaterial')} className="btn btn-primary">➕ Add Raw Material</button>
        <button onClick={() => openForm('rawMaterialIntake')} className="btn btn-secondary">📥 Stock Intake</button>
        <button onClick={() => refreshData()} className="btn btn-info">🔄 Refresh</button>
      </div>
    </div>

    {/* Raw Materials Summary Cards */}
    <div className="summary-grid">
      <div className="summary-card">
        <h3>📊 Overview</h3>
        <div className="summary-stats">
          <div className="stat">
            <span className="label">Total Raw Materials:</span>
            <span className="value">{(data.rawMaterials||[]).length}</span>
          </div>
          <div className="stat">
            <span className="label">With Stock:</span>
            <span className="value">{(stockLevels.rawMaterials||[]).filter(s => s.current_stock > 0).length}</span>
          </div>
          <div className="stat">
            <span className="label">Low Stock:</span>
            <span className="value text-danger">{(stockLevels.rawMaterials||[]).filter(s => s.current_stock <= (s.reorder_level || 0)).length}</span>
          </div>
        </div>
      </div>
    </div>

    {/* Raw Materials Table */}
    <div className="table-container">
      <table className="data-table">
        <thead>
          <tr>
            <th>SKU</th>
            <th>Name</th>
            <th>Manufacturer</th>
            <th>Unit</th>
            <th>Unit Cost (₦)</th>
            <th>Reorder Level</th>
            <th>Current Stock</th>
            <th>Stock Value (₦)</th>
            <th>Date Added</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {(data.rawMaterials||[]).length === 0 ? (
            <tr>
              <td colSpan="10" className="text-center text-muted">
                No raw materials found. Click "Add Raw Material" to get started.
              </td>
            </tr>
          ) : (
            (data.rawMaterials||[]).map(rm => {
              const stockLevel = (stockLevels.rawMaterials||[]).find(s => s.raw_material_id === rm.id);
              const currentStock = stockLevel?.current_stock || 0;
              const stockValue = currentStock * (parseFloat(rm.unit_cost) || 0);
              const isLowStock = currentStock <= (rm.reorder_level || 0);
              
              return (
                <tr key={rm.id} className={isLowStock && currentStock > 0 ? 'low-stock-row' : ''}>
                  <td>
                    <span className="sku-badge">{rm.sku}</span>
                  </td>
                  <td>
                    <strong>{rm.name}</strong>
                  </td>
                  <td>{rm.manufacturer || 'N/A'}</td>
                  <td>
                    <span className="unit-badge">{rm.unit || 'unit'}</span>
                  </td>
                  <td className="text-right">
                    {formatCurrency(rm.unit_cost || 0)}
                  </td>
                  <td className="text-center">
                    {rm.reorder_level || 0}
                  </td>
                  <td className={`text-center ${isLowStock ? 'text-danger' : ''}`}>
                    {currentStock.toFixed(2)}
                    {isLowStock && currentStock > 0 && <span className="low-stock-indicator">⚠️</span>}
                  </td>
                  <td className="text-right">
                    {formatCurrency(stockValue)}
                  </td>
                  <td>
                    {rm.created_at ? new Date(rm.created_at).toLocaleDateString() : 'N/A'}
                  </td>
                  <td>
                    <div className="action-buttons">
                      <button 
                        onClick={() => editItem('rawMaterial', rm)} 
                        className="btn btn-sm btn-info"
                        title="Edit Raw Material"
                      >
                        ✏️
                      </button>
                      <button 
                        onClick={() => openForm('rawMaterialIntake', { raw_material_id: rm.id })} 
                        className="btn btn-sm btn-success"
                        title="Add Stock"
                      >
                        📥
                      </button>
                      <button 
                        onClick={() => {
                          if (confirm(`Delete raw material "${rm.name}"? This action cannot be undone.`)) {
                            deleteItem('rawMaterial', rm.id);
                          }
                        }} 
                        className="btn btn-sm btn-danger"
                        title="Delete Raw Material"
                      >
                        🗑️
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>

    {/* Stock Level Summary */}
    {(stockLevels.rawMaterials||[]).length > 0 && (
      <div className="stock-summary">
        <h3>📈 Stock Level Summary</h3>
        <div className="summary-grid">
          <div className="summary-card">
            <h4>💰 Total Stock Value</h4>
            <div className="value-large">
              {formatCurrency((stockLevels.rawMaterials||[]).reduce((sum, s) => {
                const rm = (data.rawMaterials||[]).find(r => r.id === s.raw_material_id);
                return sum + (s.current_stock * (parseFloat(rm?.unit_cost) || 0));
              }, 0))}
            </div>
          </div>
          <div className="summary-card">
            <h4>⚠️ Low Stock Items</h4>
            <div className="low-stock-list">
              {(stockLevels.rawMaterials||[])
                .filter(s => s.current_stock <= (((data.rawMaterials||[]).find(r => r.id === s.raw_material_id))?.reorder_level || 0))
                .map(s => {
                  const rm = (data.rawMaterials||[]).find(r => r.id === s.raw_material_id);
                  return rm ? (
                    <div key={s.id} className="low-stock-item">
                      <span className="item-name">{rm.name}</span>
                      <span className="stock-level text-danger">{s.current_stock.toFixed(2)} {rm.unit}</span>
                    </div>
                  ) : null;
                })
              }
              {(stockLevels.rawMaterials||[]).filter(s => s.current_stock <= (((data.rawMaterials||[]).find(r => r.id === s.raw_material_id))?.reorder_level || 0)).length === 0 && (
                <div className="text-success">✅ All raw materials are adequately stocked</div>
              )}
            </div>
          </div>
        </div>
      </div>
    )}
  </div>
)}
