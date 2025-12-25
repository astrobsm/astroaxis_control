import React, { useState } from 'react';
import './BulkUpload.css';

const BulkUpload = ({ module, onClose, onSuccess }) => {
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState(null);
  const [selectedFile, setSelectedFile] = useState(null);

  const moduleConfig = {
    staff: {
      title: 'Staff Registration',
      templateUrl: '/api/bulk-upload/template/staff',
      uploadUrl: '/api/bulk-upload/staff',
      icon: '👥',
      description: 'Upload multiple staff records with auto-generated Employee IDs, Clock PINs, and login credentials'
    },
    products: {
      title: 'Products',
      templateUrl: '/api/bulk-upload/template/products',
      uploadUrl: '/api/bulk-upload/products',
      icon: '📦',
      description: 'Add multiple products with SKU, pricing, and inventory settings'
    },
    rawMaterials: {
      title: 'Raw Materials',
      templateUrl: '/api/bulk-upload/template/raw-materials',
      uploadUrl: '/api/bulk-upload/raw-materials',
      icon: '🧪',
      description: 'Register raw materials for production with unit costs and reorder levels'
    },
    productStockIntake: {
      title: 'Product Stock Intake',
      templateUrl: '/api/bulk-upload/template/product-stock-intake',
      uploadUrl: '/api/bulk-upload/product-stock-intake',
      icon: '📥',
      description: 'Record bulk stock intake for products across warehouses'
    },
    rawMaterialStockIntake: {
      title: 'Raw Material Stock Intake',
      templateUrl: '/api/bulk-upload/template/raw-material-stock-intake',
      uploadUrl: '/api/bulk-upload/raw-material-stock-intake',
      icon: '📥',
      description: 'Record bulk stock intake for raw materials'
    },
    warehouses: {
      title: 'Warehouses',
      templateUrl: '/api/bulk-upload/template/warehouses',
      uploadUrl: '/api/bulk-upload/warehouses',
      icon: '🏭',
      description: 'Create multiple warehouse locations'
    },
    damagedProducts: {
      title: 'Damaged Products',
      templateUrl: '/api/bulk-upload/template/damaged-products',
      uploadUrl: '/api/bulk-upload/damaged-products',
      icon: '⚠️',
      description: 'Record damaged or expired products in bulk'
    },
    damagedRawMaterials: {
      title: 'Damaged Raw Materials',
      templateUrl: '/api/bulk-upload/template/damaged-raw-materials',
      uploadUrl: '/api/bulk-upload/damaged-raw-materials',
      icon: '⚠️',
      description: 'Record damaged or contaminated raw materials'
    },
    productReturns: {
      title: 'Product Returns',
      templateUrl: '/api/bulk-upload/template/product-returns',
      uploadUrl: '/api/bulk-upload/product-returns',
      icon: '↩️',
      description: 'Process customer product returns in bulk'
    },
    bom: {
      title: 'Bill of Materials (BOM)',
      templateUrl: '/api/bulk-upload/template/bom',
      uploadUrl: '/api/bulk-upload/bom',
      icon: '📋',
      description: 'Create product formulations with raw material requirements'
    }
  };

  const config = moduleConfig[module] || {};

  const downloadTemplate = async () => {
    try {
      window.open(config.templateUrl, '_blank');
    } catch (error) {
      alert('Error downloading template: ' + error.message);
    }
  };

  const handleFileChange = (e) => {
    setSelectedFile(e.target.files[0]);
    setResult(null);
  };

  const handleUpload = async () => {
    if (!selectedFile) {
      alert('Please select a file first');
      return;
    }

    setUploading(true);
    setResult(null);

    try {
      const formData = new FormData();
      formData.append('file', selectedFile);

      const response = await fetch(config.uploadUrl, {
        method: 'POST',
        body: formData
      });

      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || 'Upload failed');
      }

      setResult(data);
      
      if (data.success) {
        setTimeout(() => {
          onSuccess && onSuccess();
        }, 3000);
      }
    } catch (error) {
      setResult({
        success: false,
        message: error.message,
        errors: [error.message]
      });
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="bulk-upload-modal">
      <div className="bulk-upload-content">
        <div className="bulk-upload-header">
          <h2>{config.icon} Bulk Upload: {config.title}</h2>
          <button onClick={onClose} className="close-btn">✖</button>
        </div>

        <div className="bulk-upload-body">
          <p className="description">{config.description}</p>

          <div className="step-section">
            <h3>📝 Step 1: Download Template</h3>
            <p>Download the Excel template with the correct format and sample data.</p>
            <button onClick={downloadTemplate} className="btn btn-primary">
              📥 Download Excel Template
            </button>
          </div>

          <div className="step-section">
            <h3>✏️ Step 2: Fill Template</h3>
            <p>Open the template in Excel, fill in your data following the format in the sample rows.</p>
            <ul className="instructions">
              <li>Required fields are marked with asterisk (*)</li>
              <li>Check the "Instructions" sheet in the template for details</li>
              <li>Do not change column headers or order</li>
              <li>Remove sample rows before uploading</li>
            </ul>
          </div>

          <div className="step-section">
            <h3>⬆️ Step 3: Upload File</h3>
            <div className="file-upload-area">
              <input 
                type="file" 
                accept=".xlsx,.xls" 
                onChange={handleFileChange}
                disabled={uploading}
              />
              {selectedFile && (
                <div className="file-selected">
                  📄 Selected: {selectedFile.name}
                </div>
              )}
            </div>
            <button 
              onClick={handleUpload} 
              className="btn btn-success"
              disabled={!selectedFile || uploading}
            >
              {uploading ? '⏳ Uploading...' : '✅ Upload and Process'}
            </button>
          </div>

          {result && (
            <div className={`result-section ${result.success ? 'success' : 'error'}`}>
              <h3>{result.success ? '✅ Upload Successful!' : '❌ Upload Failed'}</h3>
              <p className="result-message">{result.message}</p>

              {result.created && result.created.length > 0 && (
                <div className="created-items">
                  <h4>📊 Created Records ({result.created.length}):</h4>
                  <div className="created-list">
                    {result.created.slice(0, 10).map((item, idx) => (
                      <div key={idx} className="created-item">
                        <strong>Row {item.row}:</strong> {item.name || item.employee_id || 'Record created'}
                        {item.employee_id && <span> - ID: {item.employee_id}</span>}
                        {item.clock_pin && <span> - PIN: {item.clock_pin}</span>}
                      </div>
                    ))}
                    {result.created.length > 10 && (
                      <div className="more-items">
                        ... and {result.created.length - 10} more
                      </div>
                    )}
                  </div>
                </div>
              )}

              {result.created_count > 0 && (
                <div className="success-summary">
                  <strong>✅ {result.created_count} records created successfully</strong>
                </div>
              )}

              {result.errors && result.errors.length > 0 && (
                <div className="errors-section">
                  <h4>⚠️ Errors ({result.errors.length}):</h4>
                  <ul className="error-list">
                    {result.errors.slice(0, 10).map((error, idx) => (
                      <li key={idx}>{error}</li>
                    ))}
                    {result.errors.length > 10 && (
                      <li className="more-errors">... and {result.errors.length - 10} more errors</li>
                    )}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="bulk-upload-footer">
          <button onClick={onClose} className="btn btn-secondary">Close</button>
        </div>
      </div>
    </div>
  );
};

export default BulkUpload;
