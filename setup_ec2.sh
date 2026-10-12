#!/usr/bin/env bash
# ==============================================================================
# Simple College Project Setup Script for AWS EC2
# No Nginx, no complex reverse proxy - runs Flask directly on port 5000!
# ==============================================================================

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE}    Smart Campus Mobility - Quick Server Setup        ${NC}"
echo -e "${BLUE}======================================================${NC}"

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. Install minimal dependencies
echo -e "\n${YELLOW}[1/4] Installing Python, MySQL client, and build tools...${NC}"
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv default-mysql-client curl libffi-dev

# 2. Configure .env for RDS
echo -e "\n${YELLOW}[2/4] Setting up Database (.env)...${NC}"
ENV_FILE="${APP_DIR}/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo -e "${BLUE}Enter your Amazon RDS details:${NC}"
    read -p "RDS Host/Endpoint: " DB_HOST
    read -p "RDS Port [3306]: " DB_PORT
    DB_PORT=${DB_PORT:-3306}
    read -p "RDS Username [admin]: " DB_USER
    DB_USER=${DB_USER:-admin}
    read -s -p "RDS Password: " DB_PASSWORD
    echo ""
    read -p "Database Name [smart_campus_mobility]: " DB_NAME
    DB_NAME=${DB_NAME:-smart_campus_mobility}

    cat <<EOF > "$ENV_FILE"
DB_HOST='${DB_HOST}'
DB_PORT='${DB_PORT}'
DB_USER='${DB_USER}'
DB_PASSWORD='${DB_PASSWORD}'
DB_NAME='${DB_NAME}'
EOF
    echo -e "${GREEN}.env file created.${NC}"
else
    echo -e "${GREEN}Using existing .env file.${NC}"
fi

# 3. Setup Python Virtual Environment & Install Requirements
echo -e "\n${YELLOW}[3/4] Installing Python requirements (Flask, PyMySQL, mysql-connector, etc.)...${NC}"
cd "$APP_DIR"
if [ ! -d "${APP_DIR}/venv" ]; then
    python3 -m venv venv
fi

./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# 4. Initialize Database Schema & Seed Data via Python CLI
echo -e "\n${YELLOW}[4/4] Verifying Database Connection and Schema...${NC}"
./venv/bin/python3 view_db.py || true

# Fetch EC2 Public IP
PUBLIC_IP=$(curl -s -m 2 http://169.254.169.254/latest/meta-data/public-ipv4 || curl -s -m 2 ifconfig.me || echo "<YOUR-EC2-PUBLIC-IP>")

echo -e "\n${GREEN}======================================================${NC}"
echo -e "${GREEN}                    SETUP COMPLETE!                   ${NC}"
echo -e "${GREEN}======================================================${NC}"
echo -e "To start your application directly, run:"
echo -e "  ${YELLOW}./venv/bin/python3 app.py${NC}\n"
echo -e "Or to keep it running continuously in the background:"
echo -e "  ${YELLOW}nohup ./venv/bin/python3 app.py > app.log 2>&1 &${NC}\n"
echo -e "Or with production multi-worker Gunicorn:"
echo -e "  ${YELLOW}./venv/bin/gunicorn app:app -b 0.0.0.0:5000 -w 2${NC}\n"
echo -e "Then open in your browser:"
echo -e "👉 ${BLUE}http://${PUBLIC_IP}:5000${NC}\n"
echo -e "Health Check URL:"
echo -e "👉 ${BLUE}http://${PUBLIC_IP}:5000/health${NC}\n"
echo -e "${YELLOW}⚠️ IMPORTANT AWS NOTE:${NC}"
echo -e "1. In AWS EC2 Console -> Security Groups -> Edit Inbound Rules:"
echo -e "   Add: ${GREEN}Custom TCP | Port 5000 | 0.0.0.0/0${NC}"
echo -e "2. In AWS RDS Console -> Security Groups -> Edit Inbound Rules:"
echo -e "   Add: ${GREEN}MySQL/Aurora (3306) | Allow from EC2 Private IP or Security Group${NC}"
echo -e "${GREEN}======================================================${NC}"
