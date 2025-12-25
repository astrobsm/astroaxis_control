# DuckDNS Free Domain Setup for AstroBSM StockMaster

## Quick Start (5 minutes)

### Step 1: Get Your DuckDNS Token
1. Go to https://www.duckdns.org
2. Sign in with Google/GitHub/Reddit
3. Copy your token from the top of the page

### Step 2: Choose Your Subdomain
Pick a subdomain name, for example:
- `astrobsm` → **astrobsm.duckdns.org**
- `bonnesante` → **bonnesante.duckdns.org**
- `astroaxis` → **astroaxis.duckdns.org**

### Step 3: Run Setup Script
```bash
# On your DigitalOcean droplet
ssh root@209.38.226.32

# Upload and run the setup script
chmod +x setup-duckdns.sh
./setup-duckdns.sh
```

Or manually configure:

### Manual Setup Option

#### A. Update DuckDNS Domain
```bash
# Replace YOUR_SUBDOMAIN and YOUR_TOKEN
curl "https://www.duckdns.org/update?domains=YOUR_SUBDOMAIN&token=YOUR_TOKEN&ip=209.38.226.32"
```

If you see `OK`, it worked!

#### B. Set Up Auto-Update (Optional but Recommended)
```bash
# Create update script
mkdir -p ~/duckdns
cat > ~/duckdns/duck.sh << 'EOF'
#!/bin/bash
echo url="https://www.duckdns.org/update?domains=YOUR_SUBDOMAIN&token=YOUR_TOKEN&ip=" | curl -k -o ~/duckdns/duck.log -K -
EOF

chmod 700 ~/duckdns/duck.sh

# Add to crontab (updates every 5 minutes)
(crontab -l 2>/dev/null; echo "*/5 * * * * ~/duckdns/duck.sh >/dev/null 2>&1") | crontab -
```

#### C. Test Your Domain
```bash
# Wait 2-3 minutes for DNS propagation, then test
curl http://YOUR_SUBDOMAIN.duckdns.org/api/health
```

You should see:
```json
{"status":"ok","message":"AstroBSM StockMaster is running clean","version":"1.0.0"}
```

### Step 4: Update Frontend (Optional)
If you want the app to use the domain name in API calls:

```javascript
// In frontend/src/AppMain.js or wherever API_BASE is defined
const API_BASE = 'http://YOUR_SUBDOMAIN.duckdns.org';
```

## SSL/HTTPS Setup (Recommended for Production)

### Option 1: Using Nginx + Let's Encrypt (Free SSL)

```bash
# Install nginx and certbot
apt-get update
apt-get install -y nginx certbot python3-certbot-nginx

# Create nginx config
cat > /etc/nginx/sites-available/astrobsm << 'EOF'
server {
    listen 80;
    server_name YOUR_SUBDOMAIN.duckdns.org;
    
    location / {
        proxy_pass http://localhost:8004;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

# Enable site
ln -s /etc/nginx/sites-available/astrobsm /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx

# Get SSL certificate
certbot --nginx -d YOUR_SUBDOMAIN.duckdns.org --email astrobsm@gmail.com --agree-tos --non-interactive

# Auto-renewal is handled by certbot
```

Your app will now be available at **https://YOUR_SUBDOMAIN.duckdns.org** 🔒

### Option 2: Cloudflare Free SSL (Alternative)

1. Sign up at https://cloudflare.com (free tier)
2. Add your DuckDNS domain
3. Update nameservers (follow Cloudflare instructions)
4. Enable SSL in Cloudflare dashboard
5. Set SSL mode to "Flexible" or "Full"

## Testing

```bash
# Test HTTP
curl http://YOUR_SUBDOMAIN.duckdns.org/api/health

# Test HTTPS (if configured)
curl https://YOUR_SUBDOMAIN.duckdns.org/api/health

# Check from browser
# Visit: http://YOUR_SUBDOMAIN.duckdns.org
```

## Troubleshooting

### Domain not resolving
- Wait 5-10 minutes for DNS propagation
- Check DuckDNS website to verify domain is active
- Run: `nslookup YOUR_SUBDOMAIN.duckdns.org`

### SSL certificate issues
```bash
# Check nginx logs
tail -f /var/log/nginx/error.log

# Verify certbot
certbot certificates

# Renew manually if needed
certbot renew --dry-run
```

### Update domain IP if server changes
```bash
curl "https://www.duckdns.org/update?domains=YOUR_SUBDOMAIN&token=YOUR_TOKEN&ip=NEW_IP"
```

## Example Domains
- http://astrobsm.duckdns.org
- http://bonnesante.duckdns.org
- http://stockmaster.duckdns.org

## Benefits of DuckDNS
✅ **Free forever**
✅ No credit card required
✅ Easy to set up (5 minutes)
✅ Works with Let's Encrypt for free SSL
✅ Auto-update with cron
✅ Better than IP address (easy to remember)
✅ Professional appearance

## Alternative Free Domain Providers
- **No-IP** (noip.com) - 1 free hostname
- **FreeDNS** (freedns.afraid.org) - Multiple domains
- **Dynu** (dynu.com) - Free DNS

## Current Setup
- **Server IP**: 209.38.226.32
- **Current Access**: http://209.38.226.32
- **App**: AstroBSM StockMaster
- **Backend Port**: 8004 (Docker)

---

**Need help?** Check https://www.duckdns.org/install.jsp for detailed guides.
