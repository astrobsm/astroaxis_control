from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import staff, attendance, sales, products

# Create a clean test app
app = FastAPI(
    title='ASTRO-ASIX ERP - Staff Module Test',
    description='Testing Staff Registration, Payroll, and Attendance functionality',
    version='1.0.0'
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Include our new modules
app.include_router(staff.router)
app.include_router(attendance.router)
app.include_router(sales.router)  # For testing integration
app.include_router(products.router)  # For testing dependencies

@app.get('/api/health')
async def health():
    return {
        'status': 'ok',
        'message': 'ASTRO-ASIX ERP Staff Module Test Server',
        'modules': ['staff', 'attendance', 'sales', 'products']
    }

@app.get('/')
async def root():
    return {
        'message': 'ASTRO-ASIX ERP Staff Module Test',
        'api_docs': '/docs',
        'endpoints': {
            'staff_crud': '/api/staff/staffs',
            'payroll_calculation': '/api/staff/payroll/calculate',
            'payslip_pdf': '/api/staff/payslip/{payroll_id}/pdf',
            'clock_in': '/api/attendance/clock-in',
            'clock_out': '/api/attendance/clock-out',
            'attendance_list': '/api/attendance/'
        }
    }