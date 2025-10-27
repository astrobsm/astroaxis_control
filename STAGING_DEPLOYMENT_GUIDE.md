# DigitalOcean Staging Deployment Guide

## Prerequisites
- DigitalOcean account
- Domain name (optional but recommended)
- SSH client installed
- Git installed locally

## Step 1: Create DigitalOcean Droplet

### 1.1 Create Droplet
1. Log in to DigitalOcean
2. Click **Create** → **Droplets**
3. Choose configuration:
   - **Image**: Ubuntu 22.04 LTS (x64)
   - **Plan**: Basic ($12/month)
     - 2 GB RAM / 1 CPU
     - 50 GB SSD
     - 2 TB transfer
   - **Datacenter**: Choose closest to your users
   - **Authentication**: SSH Key (recommended) or Password
   - **Hostname**: `astroaxis-staging`
4. Click **Create Droplet**
5. Note the IP address (e.g., 137.184.XXX.XXX)

### 1.2 Create Managed PostgreSQL Database (Recommended)
1. Click **Create** → **Databases**
2. Choose configuration:
   - **Database Engine**: PostgreSQL 14
   - **Plan**: Basic ($15/month)
     - 1 GB RAM / 1 vCPU
     - 10 GB Disk
   - **Datacenter**: Same as droplet
   - **Database name**: `astroaxis-staging-db`
3. Click **Create Database**
4. Note connection details:
   - Host
   - Port
   - Username
   - Password
   - Database name

**OR Use Droplet PostgreSQL (Budget Option)**
- Install PostgreSQL on the same droplet (instructions below)
- Less cost but shared resources

## Step 2: Initial Server Setup

### 2.1 Connect to Server
```bash
# Using SSH key
ssh root@YOUR_DROPLET_IP

# Using password
ssh root@YOUR_DROPLET_IP
# Enter password when prompted
```

### 2.2 Update System
```bash
apt update
apt upgrade -y
```

### 2.3 Create Non-Root User
```bash
# Create user
adduser astroaxis
# Follow prompts to set password

# Add to sudo group
usermod -aG sudo astroaxis

# Switch to new user
su - astroaxis
```

### 2.4 Setup Firewall
```bash
# Allow SSH
sudo ufw allow OpenSSH

# Allow HTTP
sudo ufw allow 80/tcp

# Allow HTTPS
sudo ufw allow 443/tcp

# Enable firewall
sudo ufw enable
sudo ufw status
```

## Step 3: Install Dependencies

### 3.1 Install Python 3.11
```bash
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.11 python3.11-venv python3.11-dev
```

### 3.2 Install PostgreSQL (if not using managed database)
```bash
sudo apt install -y postgresql postgresql-contrib
sudo systemctl start postgresql
sudo systemctl enable postgresql

# Create database and user
sudo -u postgres psql
```

In PostgreSQL console:
```sql
CREATE DATABASE axis_db;
CREATE USER astroaxis WITH PASSWORD 'your_secure_password_here';
ALTER ROLE astroaxis SET client_encoding TO 'utf8';
ALTER ROLE astroaxis SET default_transaction_isolation TO 'read committed';
ALTER ROLE astroaxis SET timezone TO 'Africa/Lagos';
GRANT ALL PRIVILEGES ON DATABASE axis_db TO astroaxis;
\q
```

### 3.3 Install Node.js 18
```bash
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt install -y nodejs
node --version
npm --version
```

### 3.4 Install Nginx
```bash
sudo apt install -y nginx
sudo systemctl start nginx
sudo systemctl enable nginx
```

### 3.5 Install Git
```bash
sudo apt install -y git
git --version
```

## Step 4: Deploy Application

### 4.1 Clone Repository
```bash
cd /home/astroaxis
git clone https://github.com/astrobsm/astroaxis_control.git
cd astroaxis_control
```

### 4.2 Setup Backend

```bash
# Navigate to backend
cd /home/astroaxis/astroaxis_control/backend

# Create virtual environment
python3.11 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install dependencies
pip install -r requirements.txt
```

### 4.3 Configure Environment Variables
```bash
# Create .env file
nano .env
```

Add the following (adjust values):
```env
# Database Configuration (Managed Database)
DATABASE_URL=postgresql+asyncpg://doadmin:PASSWORD@db-postgresql-nyc1-12345.ondigitalocean.com:25060/axis_db?ssl=require

# OR for local PostgreSQL
# DATABASE_URL=postgresql+asyncpg://astroaxis:your_secure_password_here@localhost:5432/axis_db

# Security
SECRET_KEY=your-super-secret-key-change-this-in-production-min-32-chars
DEBUG=False

# CORS (use your domain)
CORS_ORIGINS=http://staging.yourdomain.com,https://staging.yourdomain.com

# Application
ENVIRONMENT=staging
```

Save and exit (Ctrl+X, Y, Enter)

### 4.4 Run Database Migrations
```bash
# Make sure venv is activated
source venv/bin/activate

# Run migrations
alembic upgrade head
```

### 4.5 Build Frontend
```bash
# Navigate to frontend
cd /home/astroaxis/astroaxis_control/frontend

# Install dependencies
npm install

# Build for production
npm run build
```

## Step 5: Configure Nginx

### 5.1 Create Nginx Configuration
```bash
sudo nano /etc/nginx/sites-available/astroaxis-staging
```

Add configuration:
```nginx
server {
    listen 80;
    server_name YOUR_DROPLET_IP;  # Or staging.yourdomain.com
    
    client_max_body_size 100M;

    # Serve frontend
    location / {
        root /home/astroaxis/astroaxis_control/frontend/build;
        try_files $uri $uri/ /index.html;
        
        # Cache static assets
        location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {
            expires 1y;
            add_header Cache-Control "public, immutable";
        }
        
        # Service Worker - no cache
        location = /serviceWorker.js {
            add_header Cache-Control "no-cache, no-store, must-revalidate";
            add_header Service-Worker-Allowed "/";
        }
    }

    # Proxy API requests to backend
    location /api {
        proxy_pass http://127.0.0.1:8004;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }
}
```

Save and exit.

### 5.2 Enable Site
```bash
# Create symbolic link
sudo ln -s /etc/nginx/sites-available/astroaxis-staging /etc/nginx/sites-enabled/

# Remove default site
sudo rm /etc/nginx/sites-enabled/default

# Test configuration
sudo nginx -t

# Restart Nginx
sudo systemctl restart nginx
```

## Step 6: Setup Systemd Service for Backend

### 6.1 Create Service File
```bash
sudo nano /etc/systemd/system/astroaxis.service
```

Add configuration:
```ini
[Unit]
Description=ASTRO-ASIX ERP Backend
After=network.target postgresql.service

[Service]
Type=simple
User=astroaxis
Group=astroaxis
WorkingDirectory=/home/astroaxis/astroaxis_control/backend
Environment="PATH=/home/astroaxis/astroaxis_control/backend/venv/bin"
ExecStart=/home/astroaxis/astroaxis_control/backend/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8004 --workers 2
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Save and exit.

### 6.2 Start Service
```bash
# Reload systemd
sudo systemctl daemon-reload

# Start service
sudo systemctl start astroaxis

# Enable auto-start on boot
sudo systemctl enable astroaxis

# Check status
sudo systemctl status astroaxis
```

## Step 7: Setup SSL (Optional but Recommended)

### 7.1 Install Certbot
```bash
sudo apt install -y certbot python3-certbot-nginx
```

### 7.2 Obtain SSL Certificate
```bash
# Make sure your domain points to your droplet IP first
sudo certbot --nginx -d staging.yourdomain.com
```

Follow prompts:
- Enter email
- Agree to terms
- Choose redirect HTTP to HTTPS (option 2)

Certificate will auto-renew.

## Step 8: Testing

### 8.1 Test Backend
```bash
curl http://localhost:8004/api/health
# Should return: {"status":"ok","message":"ASTRO-ASIX ERP is running clean","version":"1.0.0"}
```

### 8.2 Test Frontend
Open browser and navigate to:
- http://YOUR_DROPLET_IP
- OR https://staging.yourdomain.com (if SSL configured)

### 8.3 Test Full Stack
1. Register a new user
2. Create a staff member
3. Clock in/out for attendance
4. Create a product
5. Verify data persistence

## Step 9: Monitoring and Logs

### 9.1 View Backend Logs
```bash
# Real-time logs
sudo journalctl -u astroaxis -f

# Recent logs
sudo journalctl -u astroaxis -n 100

# Today's logs
sudo journalctl -u astroaxis --since today
```

### 9.2 View Nginx Logs
```bash
# Access logs
sudo tail -f /var/log/nginx/access.log

# Error logs
sudo tail -f /var/log/nginx/error.log
```

### 9.3 View PostgreSQL Logs (if local)
```bash
sudo tail -f /var/log/postgresql/postgresql-14-main.log
```

## Step 10: Maintenance

### 10.1 Update Application
```bash
cd /home/astroaxis/astroaxis_control

# Pull latest code
git pull origin main

# Update backend
cd backend
source venv/bin/activate
pip install -r requirements.txt
alembic upgrade head

# Update frontend
cd ../frontend
npm install
npm run build

# Restart backend
sudo systemctl restart astroaxis

# Reload nginx (if config changed)
sudo systemctl reload nginx
```

### 10.2 Database Backup
```bash
# Create backup directory
mkdir -p /home/astroaxis/backups

# Backup database
sudo -u postgres pg_dump axis_db > /home/astroaxis/backups/axis_db_$(date +%Y%m%d_%H%M%S).sql

# Or with managed database
PGPASSWORD=your_password pg_dump -h your-db-host -U doadmin -d axis_db > backup.sql
```

### 10.3 Setup Automated Backups
```bash
# Create backup script
nano /home/astroaxis/backup.sh
```

Add:
```bash
#!/bin/bash
BACKUP_DIR="/home/astroaxis/backups"
DATE=$(date +%Y%m%d_%H%M%S)
sudo -u postgres pg_dump axis_db > $BACKUP_DIR/axis_db_$DATE.sql
find $BACKUP_DIR -name "*.sql" -mtime +7 -delete
```

Make executable and add to cron:
```bash
chmod +x /home/astroaxis/backup.sh
crontab -e
# Add: 0 2 * * * /home/astroaxis/backup.sh
```

## Troubleshooting

### Backend Not Starting
```bash
# Check service status
sudo systemctl status astroaxis

# Check logs
sudo journalctl -u astroaxis -n 50

# Common issues:
# - Database connection: Check DATABASE_URL in .env
# - Port in use: sudo lsof -i :8004
# - Permissions: Check file ownership
```

### Nginx Errors
```bash
# Test configuration
sudo nginx -t

# Check error logs
sudo tail -f /var/log/nginx/error.log

# Common issues:
# - Syntax errors in config
# - Frontend build directory not found
# - Permissions on static files
```

### Database Connection Issues
```bash
# Test PostgreSQL connection
psql -h localhost -U astroaxis -d axis_db

# For managed database
psql "postgresql://user:pass@host:port/dbname?sslmode=require"

# Check PostgreSQL status
sudo systemctl status postgresql
```

## Security Checklist

- ✅ Firewall enabled (UFW)
- ✅ Non-root user created
- ✅ SSH key authentication (disable password login)
- ✅ SSL certificate installed
- ✅ DEBUG=False in production
- ✅ Strong SECRET_KEY generated
- ✅ Database user has limited permissions
- ✅ Regular backups scheduled
- ✅ Keep system updated: `sudo apt update && sudo apt upgrade`

## Cost Estimate (Staging)

- Droplet (2GB): $12/month
- Managed PostgreSQL (optional): $15/month
- Domain (optional): $12/year
- **Total**: $12-27/month

## Next Steps

1. Test thoroughly on staging
2. Fix any issues found
3. Document changes
4. Prepare production deployment
5. Setup monitoring (optional): DigitalOcean Monitoring, UptimeRobot

## Support Resources

- DigitalOcean Community: https://www.digitalocean.com/community
- Ubuntu Documentation: https://help.ubuntu.com/
- PostgreSQL Docs: https://www.postgresql.org/docs/
- Nginx Docs: https://nginx.org/en/docs/
