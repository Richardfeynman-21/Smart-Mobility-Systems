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
echo -e "\n${YELLOW}[1/4] Installing Python and MySQL client...${NC}"
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv default-mysql-client curl

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
DB_HOST=${DB_HOST}
DB_PORT=${DB_PORT}
DB_USER=${DB_USER}
DB_PASSWORD=${DB_PASSWORD}
DB_NAME=${DB_NAME}
EOF
    echo -e "${GREEN}.env file created.${NC}"
else
    echo -e "${GREEN}Using existing .env file.${NC}"
fi

# Load variables
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# 3. Load Schema into RDS
echo -e "\n${YELLOW}[3/4] Importing database schema into RDS...${NC}"
if mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASSWORD" -e "SELECT 1;" >/dev/null 2>&1; then
    mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASSWORD" "$DB_NAME" < "${APP_DIR}/database/schema.sql"
    echo -e "${GREEN}Database tables & sample data loaded successfully!${NC}"
else
    echo -e "${YELLOW}Could not connect to RDS right now. Please verify your RDS Security Group allows port 3306.${NC}"
fi

# 4. Setup Python Virtual Environment & Install Requirements
echo -e "\n${YELLOW}[4/4] Installing Python requirements...${NC}"
cd "$APP_DIR"
if [ ! -d "${APP_DIR}/venv" ]; then
    python3 -m venv venv
fi

./venv/bin/pip install --upgrade pip
./venv/bin/pip install -r requirements.txt

# Fetch EC2 Public IP
PUBLIC_IP=$(curl -s -m 2 http://169.254.169.254/latest/meta-data/public-ipv4 || curl -s -m 2 ifconfig.me || echo "<YOUR-EC2-PUBLIC-IP>")

echo -e "\n${GREEN}======================================================${NC}"
echo -e "${GREEN}                    SETUP COMPLETE!                   ${NC}"
echo -e "${GREEN}======================================================${NC}"
echo -e "To start your application directly, run:"
echo -e "  ${YELLOW}./venv/bin/python3 app.py${NC}\n"
echo -e "Or to keep it running in the background:"
echo -e "  ${YELLOW}nohup ./venv/bin/python3 app.py > app.log 2>&1 &${NC}\n"
echo -e "Then open in your browser:"
echo -e "👉 ${BLUE}http://${PUBLIC_IP}:5000${NC}\n"
echo -e "${YELLOW}⚠️ IMPORTANT AWS NOTE:${NC}"
echo -e "In your AWS EC2 Console -> Security Groups -> Edit Inbound Rules:"
echo -e "Add a rule for: ${GREEN}Custom TCP | Port 5000 | 0.0.0.0/0${NC}"
echo -e "${GREEN}======================================================${NC}"
