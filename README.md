# Smart Campus Mobility

A full-stack Electric Vehicle (EV) and Smart Bicycle sharing, monitoring, and reservation management system designed for university and corporate campuses.

---

## 🚀 Features

* **Real-Time Fleet Monitoring**: Track electric scooters, bicycles, and campus EVs across campus hubs.
* **Student Self-Service Portal**: Select student profiles to reserve and return available vehicles.
* **Intelligent Vehicle Status Tracking**: Real-time statuses (`AVAILABLE`, `RESERVED`, `IN_USE`, `MAINTENANCE`).
* **Automated Fleet Health Alerts**: Proactively flags low battery ($\le 20\%$) and vehicles requiring maintenance.
* **Production Ready**: Fully configured for AWS EC2 and Amazon RDS MySQL with automated deployment scripts.

---

## 🛠️ Tech Stack

* **Backend**: Python 3, Flask, Gunicorn
* **Database**: MySQL 8.0 / Amazon RDS MySQL
* **Frontend**: Vanilla HTML5, Modern CSS3, JavaScript (Fetch API)
* **DevOps**: Docker, Nginx (Reverse Proxy), Systemd, Bash automation

---

## 📂 Project Structure

```
SmartCampusMobility/
├── app.py                  # Flask backend & REST API
├── requirements.txt        # Python package dependencies
├── setup_ec2.sh            # Automated deployment script for AWS EC2
├── Dockerfile              # Docker container definition
├── .dockerignore           # Docker ignore rules
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

### 1. Prerequisites
* Python 3.10+
* MySQL 8.0+

### 2. Setup Database
```bash
mysql -u root -p < database/schema.sql
```

### 3. Setup Python Environment
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 4. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your MySQL credentials:
```bash
cp .env.example .env
```

### 5. Run Application
```bash
python3 app.py
```
Open your browser and navigate to `http://localhost:5000`.

---

## ☁️ Deploying to AWS (EC2 + RDS)

1. Provision an **Amazon RDS MySQL** instance inside a VPC.
2. Launch an **Ubuntu 24.04 EC2** instance (`t3.micro`).
3. Clone this repository on the EC2 instance:
   ```bash
   git clone https://github.com/Richardfeynman-21/Smart-Mobility-Systems.git
   cd Smart-Mobility-Systems
   ```
4. Run the automated setup script:
   ```bash
   sudo ./setup_ec2.sh
   ```
The script handles system dependencies, RDS database migration, Gunicorn systemd service, and Nginx reverse proxy automatically.

---

## 📡 REST API Reference

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/vehicles` | List all vehicles |
| `POST` | `/vehicles` | Register a new vehicle |
| `PUT` | `/vehicles/<id>` | Update vehicle status or location |
| `DELETE` | `/vehicles/<id>` | Delete a vehicle |
| `GET` | `/users` | List registered campus users |
| `GET` | `/reservations` | List all reservations |
| `POST` | `/reservations` | Reserve an available vehicle |
| `PUT` | `/reservations/<id>/return` | Return a vehicle |
| `GET` | `/vehicles/alerts` | List fleet alerts (low battery / maintenance) |
