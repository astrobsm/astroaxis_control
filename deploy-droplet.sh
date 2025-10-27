#!/bin/bash
# ASTRO-ASIX ERP - Automated Droplet Deployment Script
# This script automates the deployment to a DigitalOcean Droplet

set -e  # Exit on any error

echo "🚀 ASTRO-ASIX ERP Droplet Deployment"
echo "===================================="
echo ""

# Check if Docker is installed
if ! command -v docker &> /dev/null; then
    echo "❌ Docker not found. Installing Docker..."
    curl -fsSL https://get.docker.com -o get-docker.sh
    sudo sh get-docker.sh
    sudo systemctl enable docker
    sudo systemctl start docker
    rm get-docker.sh
    echo "✅ Docker installed successfully"
fi

# Check if Docker Compose is available
if ! docker compose version &> /dev/null; then
    echo "❌ Docker Compose not found. Please install Docker Compose."
    exit 1
fi

echo "✅ Docker and Docker Compose are available"
echo ""

# Clone or update repository
if [ -d "astroaxis_control" ]; then
    echo "📥 Updating existing repository..."
    cd astroaxis_control
    git pull origin main
else
    echo "📥 Cloning repository..."
    git clone https://github.com/astrobsm/astroaxis_control.git
    cd astroaxis_control
fi

echo "✅ Repository ready"
echo ""

# Generate secure random secrets
echo "🔐 Generating secure secrets..."
SECRET_KEY=$(openssl rand -base64 32)
DB_PASSWORD=$(openssl rand -base64 24 | tr -d "=+/" | cut -c1-20)

# Create .env file
echo "📝 Creating .env file..."
cat > .env << EOF
# Database Configuration
DB_PASSWORD=${DB_PASSWORD}

# Backend Configuration
SECRET_KEY=${SECRET_KEY}
DEBUG=False
ENVIRONMENT=production

# Optional: SMTP Configuration (for email notifications)
# SMTP_SERVER=smtp.gmail.com
# SMTP_PORT=587
# SMTP_USERNAME=your-email@gmail.com
# SMTP_PASSWORD=your-app-password
# SMTP_FROM=noreply@astroaxis.com
EOF

echo "✅ Environment configuration created"
echo ""

# Stop existing containers (if any)
echo "🛑 Stopping existing containers..."
docker compose down 2>/dev/null || true

# Pull latest images
echo "📦 Pulling Docker images..."
docker compose pull

# Build and start containers
echo "🏗️  Building and starting containers..."
docker compose up -d --build

# Wait for services to be ready
echo "⏳ Waiting for services to start..."
sleep 30

# Check service health
echo "🏥 Checking service health..."
if docker compose ps | grep -q "healthy"; then
    echo "✅ Services are healthy!"
else
    echo "⚠️  Services started but health check pending..."
fi

echo ""
echo "=========================================="
echo "✅ DEPLOYMENT COMPLETE!"
echo "=========================================="
echo ""
echo "📋 Deployment Information:"
echo "  • Application URL: http://$(curl -s ifconfig.me)"
echo "  • Database Password: ${DB_PASSWORD}"
echo "  • Secret Key: ${SECRET_KEY}"
echo ""
echo "⚠️  IMPORTANT: Save these credentials securely!"
echo ""
echo "📊 Management Commands:"
echo "  • View logs:    docker compose logs -f"
echo "  • Restart:      docker compose restart"
echo "  • Stop:         docker compose down"
echo "  • Update:       git pull && docker compose up -d --build"
echo ""
echo "🎉 Your ERP system is now live!"
