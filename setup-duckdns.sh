#!/bin/bash

# DuckDNS Setup Script for AstroBSM StockMaster
# This script sets up a free domain using DuckDNS

echo "=================================="
echo "DuckDNS Setup for AstroBSM StockMaster"
echo "=================================="
echo ""

# Get user input
read -p "Enter your desired subdomain (e.g., 'astrobsm' for astrobsm.duckdns.org): " SUBDOMAIN
read -p "Enter your DuckDNS token (from https://www.duckdns.org): " TOKEN
SERVER_IP="209.38.226.32"

echo ""
echo "Setting up ${SUBDOMAIN}.duckdns.org → $SERVER_IP"
echo ""

# Update DuckDNS
RESPONSE=$(curl -s "https://www.duckdns.org/update?domains=${SUBDOMAIN}&token=${TOKEN}&ip=${SERVER_IP}")

if [ "$RESPONSE" == "OK" ]; then
    echo "✅ DuckDNS domain successfully configured!"
    echo "Your domain: https://${SUBDOMAIN}.duckdns.org"
    echo ""
    
    # Create cron job for auto-update
    echo "Setting up auto-update cron job..."
    CRON_CMD="*/5 * * * * curl -s \"https://www.duckdns.org/update?domains=${SUBDOMAIN}&token=${TOKEN}&ip=\" >/dev/null 2>&1"
    
    # Add to crontab if not already present
    (crontab -l 2>/dev/null | grep -v "duckdns.org/update"; echo "$CRON_CMD") | crontab -
    
    echo "✅ Auto-update cron job configured (updates every 5 minutes)"
    echo ""
    echo "Next steps:"
    echo "1. Wait 2-3 minutes for DNS propagation"
    echo "2. Access your app at: http://${SUBDOMAIN}.duckdns.org"
    echo "3. (Optional) Set up SSL certificate with certbot for HTTPS"
    echo ""
else
    echo "❌ Failed to configure DuckDNS. Response: $RESPONSE"
    echo "Please check your subdomain and token."
    exit 1
fi

# Optional: Set up SSL with Let's Encrypt
read -p "Do you want to set up SSL certificate (HTTPS)? (y/n): " SETUP_SSL

if [ "$SETUP_SSL" == "y" ] || [ "$SETUP_SSL" == "Y" ]; then
    echo ""
    echo "Installing certbot..."
    apt-get update -qq
    apt-get install -y -qq certbot python3-certbot-nginx
    
    echo "Configuring nginx..."
    
    # Create nginx config for domain
    cat > /etc/nginx/sites-available/astrobsm << EOF
server {
    listen 80;
    server_name ${SUBDOMAIN}.duckdns.org;
    
    location / {
        proxy_pass http://localhost:8004;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF
    
    # Enable site
    ln -sf /etc/nginx/sites-available/astrobsm /etc/nginx/sites-enabled/
    nginx -t && systemctl reload nginx
    
    echo "Obtaining SSL certificate..."
    certbot --nginx -d ${SUBDOMAIN}.duckdns.org --non-interactive --agree-tos --email astrobsm@gmail.com
    
    if [ $? -eq 0 ]; then
        echo "✅ SSL certificate installed successfully!"
        echo "🔒 Your app is now available at: https://${SUBDOMAIN}.duckdns.org"
    else
        echo "⚠️ SSL setup had issues. Your app is still accessible at: http://${SUBDOMAIN}.duckdns.org"
    fi
fi

echo ""
echo "Setup complete!"
