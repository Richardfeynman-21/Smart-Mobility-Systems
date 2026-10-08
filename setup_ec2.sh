#!/usr/bin/env bash
# ==============================================================================
# Smart Campus Mobility - Automated AWS EC2 Deployment Script
# Target OS: Ubuntu 22.04 / 24.04 LTS on AWS EC2
# ==============================================================================

set -e

# Color helpers
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE}   Smart Campus Mobility - AWS EC2 Setup & Deploy     ${NC}"
echo -e "${BLUE}======================================================${NC}"

# 1. Check Root / Sudo privileges
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR] Please run this script with sudo: sudo ./setup_ec2.sh${NC}"
    exit 1
fi

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_USER="${SUDO_USER:-$USER}"

echo -e "${YELLOW}[1/7] Application directory: ${APP_DIR}${NC}"
echo -e "${YELLOW}      Running as user:        ${APP_USER}${NC}"

# 2. Update System Packages & Install Dependencies
echo -e "\n${YELLOW}[2/7] Installing system packages (Python, Nginx, MySQL client)...${NC}"
apt-get update -y
apt-get install -y python3 python3-pip python3-venv nginx default-mysql-client curl

# 3. Configure .env file for AWS RDS
echo -e "\n${YELLOW}[3/7] Configuring Database & Environment Settings (.env)...${NC}"

ENV_FILE="${APP_DIR}/.env"

if [ -f "$ENV_FILE" ]; then
    echo -e "${GREEN}Found existing .env file. Keeping existing configuration.${NC}"
    read -p "Do you want to re-enter RDS credentials? (y/N): " RECONFIGURE_DB
else
    RECONFIGURE_DB="y"
fi

if [[ "$RECONFIGURE_DB" =~ ^[Yy]$ ]]; then
    echo -e "${BLUE}--- Enter your Amazon RDS details ---${NC}"
    read -p "RDS Endpoint (Host): " DB_HOST
    read -p "RDS Port [3306]: " DB_PORT
    DB_PORT=${DB_PORT:-3306}
    read -p "RDS Master Username [admin]: " DB_USER
    DB_USER=${DB_USER:-admin}
    read -s -p "RDS Master Password: " DB_PASSWORD
    echo ""
    read -p "RDS Database Name [smart_campus_mobility]: " DB_NAME
    DB_NAME=${DB_NAME:-smart_campus_mobility}

    cat <<EOF > "$ENV_FILE"
DB_HOST=${DB_HOST}
DB_PORT=${DB_PORT}
DB_USER=${DB_USER}
DB_PASSWORD=${DB_PASSWORD}
DB_NAME=${DB_NAME}
EOF
    chown "${APP_USER}:${APP_USER}" "$ENV_FILE"
    chmod 600 "$ENV_FILE"
    echo -e "${GREEN}Created ${ENV_FILE} successfully.${NC}"
fi

# Load variables from .env
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# 4. Initialize Database Schema in RDS
echo -e "\n${YELLOW}[4/7] Testing RDS connection & initializing schema...${NC}"
if [ -n "$DB_HOST" ] && [ "$DB_HOST" != "localhost" ]; then
    echo -e "Attempting connection to RDS instance at ${DB_HOST}..."
    if mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASSWORD" -e "SELECT 1;" >/dev/null 2>&1; then
        echo -e "${GREEN}RDS connection successful!${NC}"
        read -p "Import database/schema.sql into RDS now? (Y/n): " IMPORT_SCHEMA
        IMPORT_SCHEMA=${IMPORT_SCHEMA:-y}
        if [[ "$IMPORT_SCHEMA" =~ ^[Yy]$ ]]; then
            echo "Applying schema to RDS database '${DB_NAME}'..."
            mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" < "${APP_DIR}/database/schema.sql"
            echo -e "${GREEN}Database schema and seed data loaded successfully!${NC}"
        fi
    else
        echo -e "${RED}[WARNING] Could not connect to RDS right now.${NC}"
        echo -e "${YELLOW}Ensure your RDS Security Group allows inbound port 3306 from this EC2 instance.${NC}"
        echo -e "${YELLOW}Skipping automatic schema migration for now.${NC}"
    fi
else
    echo "Local MySQL specified. Skipping remote RDS auto-import."
fi

# 5. Setup Python Virtual Environment & Requirements
echo -e "\n${YELLOW}[5/7] Setting up Python virtual environment...${NC}"
cd "$APP_DIR"

if [ ! -d "${APP_DIR}/venv" ]; then
    sudo -u "$APP_USER" python3 -m venv "${APP_DIR}/venv"
fi

sudo -u "$APP_USER" "${APP_DIR}/venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "${APP_DIR}/venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
echo -e "${GREEN}Python dependencies installed successfully.${NC}"

# 6. Configure Systemd Service for Gunicorn
echo -e "\n${YELLOW}[6/7] Setting up Systemd background service for Gunicorn...${NC}"
SERVICE_FILE="/etc/systemd/system/smartcampus.service"

cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=Gunicorn service for Smart Campus Mobility
After=network.target

[Service]
User=${APP_USER}
Group=www-data
WorkingDirectory=${APP_DIR}
Environment="PATH=${APP_DIR}/venv/bin"
ExecStart=${APP_DIR}/venv/bin/gunicorn --workers 3 --bind 127.0.0.1:5000 --access-logfile - --error-logfile - app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable smartcampus
systemctl restart smartcampus
echo -e "${GREEN}smartcampus.service created, enabled, and started.${NC}"

# 7. Configure Nginx Reverse Proxy
echo -e "\n${YELLOW}[7/7] Configuring Nginx reverse proxy on port 80...${NC}"
NGINX_CONF="/etc/nginx/sites-available/smartcampus"

cat <<'EOF' > "$NGINX_CONF"
server {
    listen 80;
    server_name _;

    client_max_body_size 16M;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /static/ {
        alias /var/www/SmartCampusMobility/static/;
        expires 30d;
        access_log off;
    }
}
EOF

# Update static alias path to the actual current APP_DIR
sed -i "s|/var/www/SmartCampusMobility/static/|${APP_DIR}/static/|g" "$NGINX_CONF"

# Enable site and remove default site
ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/smartcampus
rm -f /etc/nginx/sites-enabled/default

# Test Nginx and reload
nginx -t
systemctl restart nginx
echo -e "${GREEN}Nginx configured and reloaded successfully.${NC}"

# Adjust firewall if UFW is active
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
    ufw allow 'Nginx Full'
fi

# Fetch Public IP if on EC2
PUBLIC_IP=$(curl -s -m 2 http://169.254.169.254/latest/meta-data/public-ipv4 || curl -s -m 2 ifconfig.me || echo "your-server-ip")

echo -e "\n${GREEN}======================================================${NC}"
echo -e "${GREEN}      DEPLOYMENT COMPLETE! APPLICATION IS LIVE        ${NC}"
echo -e "${GREEN}======================================================${NC}"
echo -e "You can now visit your app in any browser at:"
echo -e "👉 ${BLUE}http://${PUBLIC_IP}${NC}\n"
echo -e "Helpful commands:"
echo -e "  View application logs : ${YELLOW}sudo journalctl -u smartcampus -f${NC}"
echo -e "  Restart application   : ${YELLOW}sudo systemctl restart smartcampus${NC}"
echo -e "  Restart Nginx         : ${YELLOW}sudo systemctl restart nginx${NC}"
echo -e "  Check service status  : ${YELLOW}sudo systemctl status smartcampus${NC}"
echo -e "${GREEN}======================================================${NC}"
