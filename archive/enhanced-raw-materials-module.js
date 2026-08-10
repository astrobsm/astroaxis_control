// Enhanced Raw Materials Module - Comprehensive component for AppMain.js integration
// This contains the complete raw materials module with statistics, data table, and quick actions

const enhancedRawMaterialsModule = `
        <div className="module-content">
          <div className="module-header">
            <div className="module-header-left">
              <img src="/company-logo.png" alt="ASTRO-ASIX" className="module-logo" onError={(e) => { e.target.style.display = 'none'; }} />
              <h2> Raw Materials</h2>
            </div>
            <div className="module-actions">
              <button onClick={() => openForm('rawMaterial')} className="btn btn-primary"> [Add] Raw Material</button>
              <button onClick={() => openForm('rawMaterialIntake')} className="btn btn-secondary"> Stock Intake</button>
            </div>
          </div>
          
          {/* Raw Materials Statistics */}
          <div className="dashboard-stats" style={{marginBottom: '24px'}}>
            <div className="stat-card">
              <h3>Total Materials</h3>
              <p className="stat-number">{(data.rawMaterials || []).length}</p>
            </div>
            <div className="stat-card">
              <h3>Avg. Unit Cost</h3>
              <p className="stat-number">
                {(data.rawMaterials || []).length > 0 
                  ? formatCurrency((data.rawMaterials || []).reduce((sum, rm) => sum + (parseFloat(rm.unit_cost) || 0), 0) / (data.rawMaterials || []).length)
                  : formatCurrency(0)
                }
              </p>
            </div>
            <div className="stat-card">
              <h3>Total Inventory Value</h3>
              <p className="stat-number">
                {formatCurrency((data.rawMaterials || []).reduce((sum, rm) => sum + (parseFloat(rm.unit_cost) || 0), 0))}
              </p>
            </div>
            <div className="stat-card">
              <h3>Unique Manufacturers</h3>
              <p className="stat-number">
                {new Set((data.rawMaterials || []).map(rm => rm.manufacturer).filter(m => m && m.trim())).size}
              </p>
            </div>
          </div>

          {/* Raw Materials Data Table */}
          <div className="table-container">
            {(data.rawMaterials || []).length > 0 ? (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>SKU</th>
                    <th>Raw Material Name</th>
                    <th>Manufacturer</th>
                    <th>Unit</th>
                    <th>Unit Cost</th>
                    <th>Reorder Level</th>
                    <th>Date Added</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.rawMaterials || []).map(rawMaterial => (
                    <tr key={rawMaterial.id}>
                      <td><strong>{rawMaterial.sku}</strong></td>
                      <td>{rawMaterial.name}</td>
                      <td>{rawMaterial.manufacturer || 'N/A'}</td>
                      <td>{rawMaterial.unit}</td>
                      <td>{formatCurrency(rawMaterial.unit_cost)}</td>
                      <td>{rawMaterial.reorder_level}</td>
                      <td>{rawMaterial.date_added ? new Date(rawMaterial.date_added).toLocaleDateString() : 'N/A'}</td>
                      <td className="actions">
                        <button onClick={() => openForm('rawMaterial', rawMaterial)} className="btn-edit">Edit</button>
                        <button onClick={() => openForm('rawMaterialIntake', { raw_material_id: rawMaterial.id })} className="btn-add">Stock Intake</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <div className="no-data">
                <p> No raw materials found. Start by adding your first raw material!</p>
                <button onClick={() => openForm('rawMaterial')} className="btn btn-primary">Add Raw Material</button>
              </div>
            )}
          </div>

          {/* Quick Actions */}
          <div className="quick-actions" style={{marginTop: '24px'}}>
            <h3>Quick Actions</h3>
            <div className="action-buttons">
              <button onClick={() => setActiveModule('stockManagement')} className="action-btn">
                Stock Management
              </button>
              <button onClick={() => setActiveModule('production')} className="action-btn">
                Production Console
              </button>
              <button onClick={() => setActiveModule('reports')} className="action-btn">
                Reports & Analytics
              </button>
            </div>
          </div>
        </div>
`;

// Export for integration
module.exports = enhancedRawMaterialsModule;