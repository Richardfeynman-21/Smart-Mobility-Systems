# Smart Campus Mobility

A lightweight Electric Vehicle (EV) and Smart Bicycle sharing, monitoring, and reservation management system for campus fleets.

---

## 🚀 Features

* **Real-Time Fleet Monitoring**: Track electric scooters, bicycles, and campus EVs across campus hubs.
* **Student Self-Service Portal**: Select student profiles to reserve and return available vehicles.
* **Intelligent Vehicle Status Tracking**: Real-time statuses (`AVAILABLE`, `RESERVED`, `IN_USE`, `MAINTENANCE`).
* **Automated Fleet Health Alerts**: Proactively flags low battery ($\le 20\%$) and vehicles requiring maintenance.
* **Direct & Simple Deployment**: Runs Flask directly on port 5000—no complex reverse proxies needed.

---

## 🛠️ Tech Stack

* **Backend**: Python 3, Flask
* **Database**: MySQL 8.0 / Amazon RDS MySQL
* **Frontend**: HTML5, CSS3, JavaScript (Fetch API)

---

## 📂 Project Structure

```
SmartCampusMobility/
├── app.py                  # Flask backend & REST API (runs on 0.0.0.0:5000)
├── view_db.py              # CLI tool to inspect local SQLite database anytime
├── requirements.txt        # Python package dependencies
├── setup_ec2.sh            # Simple 1-click setup script for AWS EC2
├── .gitignore              # Git ignore rules
├── .env.example            # Environment variables template
├── database/
│   └── schema.sql          # MySQL database schema & seed data
├── static/
│   ├── css/
│   │   └── style.css       # Main stylesheet
│   └── js/
│       └── script.js       # Frontend application logic
└── templates/
    └── index.html          # Main dashboard view
```

---

## 💻 Local Quickstart

### 1. Setup Database
```bash
mysql -u root -p < database/schema.sql
```

### 2. Setup Python Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Database
Copy `.env.example` to `.env` and enter your MySQL details:
```bash
cp .env.example .env
```

### 4. Run the Application
```bash
python3 app.py
```
Open your browser and visit: `http://localhost:5000`

---

## ☁️ Simple AWS EC2 Deployment (with Amazon RDS)

### 1. In AWS EC2 Security Group:
Add an Inbound Rule to allow traffic to the Flask port:
* **Type**: Custom TCP
* **Port**: `5000`
* **Source**: `0.0.0.0/0` (Anywhere)

### 2. On your EC2 Terminal:
```bash
git clone https://github.com/Richardfeynman-21/Smart-Mobility-Systems.git
cd Smart-Mobility-Systems
chmod +x setup_ec2.sh
./setup_ec2.sh
```

### 3. Run the App:
```bash
# Direct run
./venv/bin/python3 app.py

# Or run in the background
nohup ./venv/bin/python3 app.py > app.log 2>&1 &
```

Then visit:
```
http://<YOUR-EC2-PUBLIC-IP>:5000
```
