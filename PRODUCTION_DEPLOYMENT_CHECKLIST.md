# Production Deployment Checklist

## Pre-Deployment Checklist

### 1. Code Quality ✅
- [ ] All features tested locally
- [ ] No console errors in browser
- [ ] No Python exceptions in logs
- [ ] All API endpoints return correct responses
- [ ] Database migrations run successfully
- [ ] Frontend builds without errors
- [ ] Code reviewed and approved
- [ ] Git repository up to date

### 2. Security ✅
- [ ] DEBUG=False in production environment
- [ ] Strong SECRET_KEY generated (min 32 characters, random)
- [ ] Database password is strong and unique
- [ ] No sensitive data in code (use environment variables)
- [ ] No API keys or credentials committed to Git
- [ ] SQL injection protection verified (using SQLAlchemy ORM)
- [ ] CORS configured for production domain only
- [ ] HTTPS/SSL certificate ready
- [ ] Security headers configured in Nginx
- [ ] Rate limiting implemented (optional but recommended)

### 3. Database ✅
- [ ] Production database created
- [ ] Database user with appropriate permissions only
- [ ] Backup strategy implemented
- [ ] Database connection string tested
- [ ] Migrations tested on staging
- [ ] Indexes optimized for performance
- [ ] Database size estimated
- [ ] Connection pooling configured

### 4. Infrastructure ✅
- [ ] Server specifications adequate for expected load
- [ ] Firewall rules configured
- [ ] Domain name purchased and configured
- [ ] DNS records pointing to server
- [ ] SSL certificate obtained
- [ ] CDN configured (optional)
- [ ] Monitoring tools set up
- [ ] Backup server/strategy (optional)

### 5. Configuration ✅
- [ ] Environment variables set correctly
- [ ] Email SMTP configured (if needed)
- [ ] SMS gateway configured (if needed)
- [ ] File upload limits set
- [ ] Session timeout configured
- [ ] Timezone set correctly (Africa/Lagos)
- [ ] Currency set to NGN (₦)
- [ ] Company information updated

### 6. Performance ✅
- [ ] Static files optimized (minified, compressed)
- [ ] Images optimized
- [ ] Database queries optimized
- [ ] Caching strategy implemented
- [ ] API response times acceptable (<200ms)
- [ ] Frontend loads quickly (<3 seconds)
- [ ] Service Worker configured correctly

### 7. Testing ✅
- [ ] All CRUD operations work
- [ ] User authentication works
- [ ] Permission system works
- [ ] Attendance clock in/out works
- [ ] Payroll calculations accurate
- [ ] PDF generation works
- [ ] File uploads work
- [ ] Cross-browser testing done (Chrome, Firefox, Edge, Safari)
- [ ] Mobile responsive design tested
- [ ] PWA install works

### 8. Documentation ✅
- [ ] README.md updated
- [ ] API documentation complete
- [ ] User guide created
- [ ] Admin guide created
- [ ] Deployment documentation updated
- [ ] Changelog maintained

### 9. Backup & Recovery ✅
- [ ] Database backup automated
- [ ] Code backup strategy (Git)
- [ ] Recovery procedure documented
- [ ] Backup tested (restore verified)
- [ ] Backup retention policy defined

### 10. Monitoring & Logging ✅
- [ ] Error logging configured
- [ ] Access logging enabled
- [ ] Monitoring dashboard set up
- [ ] Alerts configured for critical errors
- [ ] Uptime monitoring enabled
- [ ] Performance monitoring enabled

---

## Deployment Steps

### Phase 1: Preparation (Day 1)

#### Morning
- [ ] **9:00 AM**: Team meeting - Review deployment plan
- [ ] **9:30 AM**: Final code freeze - No new changes
- [ ] **10:00 AM**: Run all tests locally
  ```bash
  cd backend
  python test_complete_crud.py
  ```
- [ ] **10:30 AM**: Create final Git tag
  ```bash
  git tag -a v1.0.0 -m "Production Release 1.0.0"
  git push origin v1.0.0
  ```
- [ ] **11:00 AM**: Database backup from staging
  ```bash
  pg_dump staging_db > staging_backup.sql
  ```

#### Afternoon
- [ ] **2:00 PM**: Provision production server
  - Create DigitalOcean droplet (4GB RAM minimum)
  - Create managed PostgreSQL database
  - Configure firewall
  - Set up monitoring

- [ ] **3:00 PM**: Configure DNS
  - Point domain to server IP
  - Wait for DNS propagation (can take 1-48 hours)
  - Test with: `nslookup yourdomain.com`

- [ ] **4:00 PM**: Install SSL certificate
  ```bash
  sudo certbot --nginx -d yourdomain.com -d www.yourdomain.com
  ```

### Phase 2: Deployment (Day 2)

#### Morning
- [ ] **9:00 AM**: Deploy backend
  ```bash
  ssh user@server
  cd /home/astroaxis/astroaxis_control/backend
  git pull origin main
  source venv/bin/activate
  pip install -r requirements.txt
  ```

- [ ] **9:30 AM**: Run database migrations
  ```bash
  alembic upgrade head
  ```

- [ ] **10:00 AM**: Create initial admin user
  ```bash
  python reset_admin.py
  # Note: Update with production credentials
  ```

- [ ] **10:30 AM**: Deploy frontend
  ```bash
  cd ../frontend
  npm install
  npm run build
  ```

- [ ] **11:00 AM**: Configure Nginx
  ```bash
  sudo nano /etc/nginx/sites-available/astroaxis
  sudo nginx -t
  sudo systemctl reload nginx
  ```

- [ ] **11:30 AM**: Start backend service
  ```bash
  sudo systemctl restart astroaxis
  sudo systemctl status astroaxis
  ```

#### Afternoon
- [ ] **2:00 PM**: Smoke testing
  - Test homepage loads
  - Test login works
  - Test API health endpoint
  - Test database connection
  - Test file uploads

- [ ] **2:30 PM**: Functional testing
  - Create test staff member
  - Clock in/out test
  - Create test customer
  - Create test product
  - Create test sales order
  - Generate test payroll
  - Download test payslip PDF

- [ ] **3:30 PM**: Performance testing
  - Check API response times
  - Check page load times
  - Monitor server resources (CPU, RAM, Disk)
  - Check database connection pool

- [ ] **4:00 PM**: Security verification
  - Verify HTTPS works
  - Check CORS headers
  - Test login rate limiting
  - Verify no debug information exposed
  - Check firewall rules

- [ ] **4:30 PM**: Final review
  - Check all logs for errors
  - Verify monitoring alerts work
  - Test backup restoration
  - Document any issues

### Phase 3: Go-Live (Day 2 Evening or Day 3)

- [ ] **5:00 PM**: Final team meeting
  - Review deployment status
  - Address any concerns
  - Confirm go-live decision

- [ ] **5:30 PM**: Announce maintenance window
  - Notify users of deployment
  - Estimated completion time

- [ ] **6:00 PM**: Switch to production
  - Update DNS (if needed)
  - Enable production mode
  - Monitor closely

- [ ] **6:30 PM**: Post-deployment monitoring
  - Watch logs for 30 minutes
  - Monitor error rates
  - Check user feedback

- [ ] **7:00 PM**: Announce completion
  - Notify users system is live
  - Provide support contact information

---

## Post-Deployment Checklist

### Immediate (Within 1 Hour)
- [ ] Monitor application logs
- [ ] Check error rates
- [ ] Verify user logins working
- [ ] Monitor server resources
- [ ] Test critical features
- [ ] Respond to any urgent issues

### First 24 Hours
- [ ] Monitor system performance
- [ ] Track user feedback
- [ ] Fix any critical bugs immediately
- [ ] Document any issues found
- [ ] Update knowledge base
- [ ] Communicate with users

### First Week
- [ ] Daily system health checks
- [ ] Review logs daily
- [ ] Monitor database growth
- [ ] Check backup completion
- [ ] Gather user feedback
- [ ] Plan hotfixes if needed
- [ ] Update documentation

### First Month
- [ ] Weekly performance reviews
- [ ] Optimize slow queries
- [ ] Review security logs
- [ ] Update dependencies
- [ ] Plan feature updates
- [ ] Review backup retention
- [ ] Capacity planning

---

## Rollback Plan

### When to Rollback
- Critical security vulnerability discovered
- Data corruption detected
- System unusable for users
- Database migration failed
- Performance degradation >50%

### Rollback Procedure

1. **Stop Production Services**
   ```bash
   sudo systemctl stop astroaxis
   sudo systemctl stop nginx
   ```

2. **Restore Database**
   ```bash
   # Restore from backup
   psql axis_db < backup_before_deployment.sql
   ```

3. **Revert Code**
   ```bash
   cd /home/astroaxis/astroaxis_control
   git checkout previous-stable-tag
   cd backend
   source venv/bin/activate
   alembic downgrade -1  # If migration issue
   ```

4. **Restart Services**
   ```bash
   sudo systemctl start astroaxis
   sudo systemctl start nginx
   ```

5. **Verify Rollback**
   - Test critical features
   - Check logs
   - Notify users

6. **Document Issue**
   - What went wrong
   - Why rollback was needed
   - Lessons learned
   - Prevention plan

---

## Environment Variables Template

### Production .env File
```env
# Database
DATABASE_URL=postgresql+asyncpg://username:password@production-db-host:25060/axis_db?ssl=require

# Security
SECRET_KEY=generate-strong-random-key-min-32-chars-change-this
DEBUG=False
ENVIRONMENT=production

# CORS
CORS_ORIGINS=https://yourdomain.com,https://www.yourdomain.com

# Email (Optional)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=noreply@yourdomain.com
SMTP_PASSWORD=your-email-password
SMTP_USE_TLS=True

# SMS (Optional)
SMS_GATEWAY_URL=https://api.sms-provider.com/send
SMS_API_KEY=your-sms-api-key

# Application
COMPANY_NAME=ASTRO-ASIX
CURRENCY_CODE=NGN
CURRENCY_SYMBOL=₦
TIMEZONE=Africa/Lagos

# File Uploads
MAX_UPLOAD_SIZE_MB=10
ALLOWED_UPLOAD_TYPES=pdf,png,jpg,jpeg,gif,doc,docx,xls,xlsx

# Session
SESSION_TIMEOUT_MINUTES=30
MAX_LOGIN_ATTEMPTS=5

# Monitoring (Optional)
SENTRY_DSN=your-sentry-dsn
LOG_LEVEL=INFO
```

---

## Success Criteria

### Deployment Successful If:
✅ Application accessible via HTTPS  
✅ No errors in logs for 1 hour  
✅ Users can login and perform all operations  
✅ Database queries respond in <200ms  
✅ Page load time <3 seconds  
✅ SSL certificate valid  
✅ Backups running automatically  
✅ Monitoring alerts configured  
✅ No security vulnerabilities  
✅ Mobile responsive  

---

## Support Plan

### On-Call Schedule
- **Week 1**: Daily monitoring (8 AM - 8 PM)
- **Week 2-4**: Business hours support
- **After Month 1**: Regular support schedule

### Escalation Path
1. **Level 1**: Check logs, restart service if needed
2. **Level 2**: Database issues, rollback if critical
3. **Level 3**: Infrastructure issues, contact hosting support

### Communication Channels
- **Users**: support@yourdomain.com
- **Team**: Slack/Teams channel
- **Critical**: SMS/Phone alerts

---

## Key Contacts

| Role | Name | Contact | Responsibility |
|------|------|---------|----------------|
| Project Lead | | | Overall deployment |
| Backend Dev | | | API/Database issues |
| Frontend Dev | | | UI/UX issues |
| DevOps | | | Infrastructure |
| Database Admin | | | Database issues |
| Support Lead | | | User issues |

---

## Estimated Timeline

- **Preparation**: 1 day
- **Deployment**: 1 day
- **Testing**: 4 hours
- **Go-Live**: 2 hours
- **Monitoring**: Ongoing

**Total**: 2-3 days for full production deployment

---

## Budget Estimate

| Item | Cost (Monthly) |
|------|----------------|
| DigitalOcean Droplet (4GB) | $24 |
| Managed PostgreSQL | $15 |
| Domain Name | $1 (annual/12) |
| SSL Certificate | $0 (Let's Encrypt) |
| Monitoring (optional) | $0-29 |
| Backups (optional) | $0-10 |
| **Total** | **$40-79/month** |

---

## Final Notes

- **DO NOT** rush deployment
- **DO** thorough testing on staging first
- **DO** have rollback plan ready
- **DO** communicate with users
- **DO** monitor closely after deployment
- **DON'T** deploy on Fridays or before holidays
- **DON'T** make changes without testing
- **DON'T** forget to celebrate success! 🎉

---

**Deployment approved by**: ________________  
**Date**: ________________  
**Signature**: ________________
