# ASTRO-ASIX ERP (Bonnesante Medicals)

**Precision Production. Smart Sales. Reliable Care.**

This repository contains ASTRO-ASIX ERP: a complete FastAPI backend with BOM cost calculation engine, React PWA frontend, and PostgreSQL database for Bonnesante Medicals wound care products.

## 🏗️ Architecture

- **Backend**: FastAPI + SQLAlchemy + PostgreSQL
- **Frontend**: React PWA (no TypeScript) with offline support
- **Database**: PostgreSQL with deterministic BOM cost calculation
- **Deployment**: Unified FastAPI server serving both API and React frontend
- **Colors**: Navy Blue #0B3D91, Silver #C0C6CA, Accent Red #D32F2F

## 🚀 Quick Start (Unified Application)

The application now runs as a single unified server on port 8000, serving both the React frontend and FastAPI backend.

### Prerequisites
- PostgreSQL running with user: `postgres`, password: `natiss_natiss`
- Python 3.11+
- Node.js 18+ (for building frontend)

### Setup Instructions

1. **Create Database**
```powershell
cd backend
python scripts/create_db.py
```

2. **Build and Run (One Command)**
```powershell
# Run the automated build and serve script
.\build-and-serve.ps1
```

**Or manually:**

1. **Build Frontend**
```powershell
cd frontend
npm install
npm run build
```

2. **Start Unified Server**
```powershell
cd backend
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Access the Application
- **Main Application**: http://localhost:8000
- **API Documentation**: http://localhost:8000/docs
- **Health Check**: http://localhost:8000/api/health

## 🎯 Features

### Complete Staff Management Module ✅
- **Staff Registration** with bank details and Nigerian Naira support
- **Real-time Attendance Tracking** with clock-in/out functionality
- **Automated Payroll Calculation** based on hours worked
- **Professional PDF Payslips** with company branding
- **Comprehensive CRUD Operations** for staff management
```powershell
python scripts/seed_data.py
```

3. **Test BOM Cost Engine**
```powershell
python demo_bom.py
pytest tests/test_bom_cost.py -v
```

4. **Start Backend**
```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

5. **Install Frontend Dependencies & Start**
```powershell
cd ../frontend
npm install
npm start
```

## 🧮 BOM Cost Calculation

The system accurately computes unit costs using PostgreSQL numeric precision:

**Formula**: `Unit Cost = Σ(Material Costs) + Direct Labor + Machine Cost + Overhead Allocation`

**Sample Calculation**:
- Product: Wound Dressing
- Materials: 2 × Gauze ($0.15) + 1 × Adhesive ($0.05) = $0.35
- ✅ **Verified**: API returns `$0.350000000000`

## 🗄️ Database Schema

Key tables created automatically:
- `users` - Staff authentication & roles
- `products` - Finished goods catalog
- `raw_materials` - Material inventory
- `boms` & `bom_lines` - Bill of Materials structure
- `product_costs` - Computed costs with timestamps
- `audit_logs` - Immutable transaction log

## 📊 Company Branding

Place your company logo at:
- `frontend/public/company-logo.png` 
- Or use: `C:/Users/USER/Pictures/company-logo.png`

## 🔧 Docker Deployment

```powershell
docker-compose up --build
```

Services:
- Frontend: http://localhost:3000
- Backend API: http://localhost:8000
- Health Check: http://localhost:8000/api/health

## ✅ Completed Features

- [x] FastAPI backend with async PostgreSQL
- [x] Deterministic BOM cost calculation engine
- [x] JWT authentication scaffold
- [x] React PWA frontend structure
- [x] Database schema & migrations
- [x] Seed data & unit tests
- [x] Docker Compose setup

## 🚧 Next Implementation Steps

- [ ] Complete CRUD APIs (Products, Raw Materials, Stock)
- [ ] Production orders & sales management
- [ ] Payroll computation & staff management
- [ ] IndexedDB offline sync
- [ ] Reports dashboard with charts
- [ ] Role-based access control

---

**Contact**: astrobsm@gmail.com  
**Company**: Bonnesante Medicals  
**System**: ASTRO-ASIX ERP v0.1.0
