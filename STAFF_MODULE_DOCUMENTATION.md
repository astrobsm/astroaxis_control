# ASTRO-ASIX ERP - Staff Module Documentation

## Overview
The staff module has been enhanced with comprehensive staff registration, payroll management, attendance tracking, and payslip generation features. All currency amounts are displayed in Nigerian Naira (₦).

## New Features Implemented

### 1. Staff Registration with Bank Details
- **Endpoint**: `POST /api/staff/staffs`
- **Features**: 
  - Employee registration with personal details
  - Bank account information for payroll
  - Hourly rate configuration
  - Nigerian Naira (NGN) currency support

### 2. Timed Attendance System
- **Clock In**: `POST /api/attendance/clock-in`
- **Clock Out**: `POST /api/attendance/clock-out?staff_id={uuid}`
- **List Attendance**: `GET /api/attendance/`
- **Features**:
  - Automatic time tracking
  - Hours worked calculation
  - Period-based filtering

### 3. Automatic Payroll Calculation
- **Endpoint**: `POST /api/staff/payroll/calculate`
- **Features**:
  - Based on attendance records
  - Overtime calculation (1.5x rate for hours > 160/month)
  - Standard monthly hours: 160 (40h/week × 4 weeks)
  - Gross pay, deductions, and net pay calculation

### 4. Payslip PDF Generation
- **Endpoint**: `GET /api/staff/payslip/{payroll_id}/pdf`
- **Features**:
  - Professional PDF layout using reportlab
  - Nigerian Naira (₦) currency formatting
  - Employee details and bank information
  - Earnings breakdown with regular and overtime hours

## Database Schema Changes

### New Staff Fields (via Alembic migration)
```sql
-- Added to staff table:
bank_name VARCHAR(128)
bank_account_number VARCHAR(64)
bank_account_name VARCHAR(128)
bank_currency VARCHAR(8) DEFAULT 'NGN'
```

### New Attendance Table
```sql
CREATE TABLE attendance (
    id UUID PRIMARY KEY,
    staff_id UUID REFERENCES staff(id),
    clock_in TIMESTAMP WITH TIME ZONE NOT NULL,
    clock_out TIMESTAMP WITH TIME ZONE,
    hours_worked NUMERIC(6,2) DEFAULT 0,
    status VARCHAR(32) DEFAULT 'open',
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

## API Endpoints Summary

### Staff Management
- `POST /api/staff/staffs` - Create staff with bank details
- `GET /api/staff/staffs` - List all staff
- `GET /api/staff/staffs/{id}` - Get staff by ID
- `PUT /api/staff/staffs/{id}` - Update staff details

### Attendance Tracking
- `POST /api/attendance/clock-in` - Clock in staff member
- `POST /api/attendance/clock-out` - Clock out staff member
- `GET /api/attendance/` - List attendance records with filters

### Payroll Management
- `POST /api/staff/payroll/calculate` - Calculate payroll for period
- `GET /api/staff/payslip/{payroll_id}/pdf` - Download payslip PDF

## Frontend Integration Examples

### 1. Staff Registration
```javascript
// Create staff with bank details
const createStaff = async (staffData) => {
  const response = await fetch('/api/staff/staffs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      employee_id: 'EMP001',
      first_name: 'John',
      last_name: 'Doe',
      position: 'Developer',
      hourly_rate: 2500.00,
      bank_name: 'First Bank Nigeria',
      bank_account_number: '1234567890',
      bank_account_name: 'John Doe',
      bank_currency: 'NGN'
    })
  });
  return response.json();
};
```

### 2. Clock In/Out
```javascript
// Clock in
const clockIn = async (staffId) => {
  const response = await fetch('/api/attendance/clock-in', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      staff_id: staffId,
      notes: 'Started work'
    })
  });
  return response.json();
};

// Clock out
const clockOut = async (staffId) => {
  const response = await fetch(`/api/attendance/clock-out?staff_id=${staffId}`, {
    method: 'POST'
  });
  return response.json();
};
```

### 3. Payroll Calculation
```javascript
// Calculate payroll for a month
const calculatePayroll = async (staffId, startDate, endDate) => {
  const response = await fetch('/api/staff/payroll/calculate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      staff_id: staffId,
      pay_period_start: startDate,
      pay_period_end: endDate
    })
  });
  return response.json();
};

// Download payslip PDF
const downloadPayslip = (payrollId) => {
  window.open(`/api/staff/payslip/${payrollId}/pdf`, '_blank');
};
```

## Payroll Business Rules

1. **Standard Hours**: 160 hours per month (40 hours/week × 4 weeks)
2. **Overtime**: Hours above 160 are paid at 1.5x the hourly rate
3. **Currency**: All amounts in Nigerian Naira (₦)
4. **Deductions**: Currently set to 0 (can be enhanced for PAYE, pension, etc.)
5. **Net Pay**: Gross Pay - Deductions

## Security Considerations

- All endpoints require proper authentication (implement as needed)
- Bank details are sensitive and should be encrypted in production
- Payroll calculations should be audited and approved before payment
- PDF generation should be rate-limited to prevent abuse

## Next Steps for Frontend Integration

1. Add staff registration form with bank details
2. Create attendance dashboard with clock-in/out buttons
3. Build payroll management interface
4. Implement payslip viewing and download functionality
5. Add notifications for overtime and payroll completion

## Testing

The modules have been tested and import correctly. Use the test server at `backend/test_server.py` for isolated testing of staff and attendance functionality.

## Migration Applied

Alembic migration has been successfully applied with:
```bash
cd backend
alembic upgrade head
```

The database now includes the new staff bank fields and attendance table.