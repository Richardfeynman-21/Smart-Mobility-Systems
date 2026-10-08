import os
from flask import Flask, jsonify, request, render_template
import mysql.connector

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import sqlite3

app = Flask(__name__)

# MySQL database configuration
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "smart_campus_mobility")
DB_PORT = int(os.getenv("DB_PORT", "3306"))

class SQLiteCursorAdapter:
    def __init__(self, cursor, dictionary=False):
        self.cursor = cursor
        self.dictionary = dictionary
        self.lastrowid = None
        self.rowcount = 0

    def execute(self, query, params=None):
        query = query.replace("%s", "?")
        if params is not None:
            self.cursor.execute(query, params)
        else:
            self.cursor.execute(query)
        self.lastrowid = self.cursor.lastrowid
        self.rowcount = self.cursor.rowcount
        return self

    def fetchall(self):
        rows = self.cursor.fetchall()
        if self.dictionary:
            return [dict(row) for row in rows]
        return [tuple(row) for row in rows]

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is None:
            return None
        if self.dictionary:
            return dict(row)
        return tuple(row)

    def close(self):
        self.cursor.close()

class SQLiteConnectionAdapter:
    def __init__(self, db_path="campus_local.db"):
        self.db_path = db_path
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        c = self._conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT,
                role TEXT DEFAULT 'STUDENT',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS vehicles (
                vehicle_id TEXT PRIMARY KEY,
                vehicle_type TEXT NOT NULL,
                location TEXT NOT NULL,
                battery_level INTEGER NOT NULL DEFAULT 100,
                status TEXT NOT NULL DEFAULT 'AVAILABLE',
                user_id TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS reservations (
                reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_time TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'ACTIVE'
            );
        """)
        c.execute("SELECT COUNT(*) FROM users")
        if c.fetchone()[0] == 0:
            c.execute("""
                INSERT INTO users (user_id, name, email, role) VALUES
                ('STU-1001', 'Aarav Patel', 'aarav.patel@campus.edu', 'STUDENT'),
                ('STU-1002', 'Rishi Reddy', 'rishi.reddy@campus.edu', 'STUDENT'),
                ('STU-1003', 'Sneha Sharma', 'sneha.sharma@campus.edu', 'STUDENT'),
                ('STU-1004', 'Vikram Rao', 'vikram.rao@campus.edu', 'STUDENT'),
                ('FAC-2001', 'Dr. Meera Iyer', 'meera.iyer@campus.edu', 'FACULTY');
            """)
            c.execute("""
                INSERT INTO vehicles (vehicle_id, vehicle_type, location, battery_level, status, user_id) VALUES
                ('EV-101', 'ELECTRIC_SCOOTER', 'North Academic Gate', 85, 'AVAILABLE', NULL),
                ('EV-102', 'ELECTRIC_SCOOTER', 'Student Center Hub', 18, 'AVAILABLE', NULL),
                ('EV-103', 'ELECTRIC_CAR', 'Main Administration Plaza', 92, 'AVAILABLE', NULL),
                ('BK-201', 'SMART_BICYCLE', 'Engineering Block B', 100, 'AVAILABLE', NULL),
                ('BK-202', 'SMART_BICYCLE', 'Central Sports Complex', 100, 'RESERVED', 'STU-1001'),
                ('EV-104', 'ELECTRIC_SCOOTER', 'Hostel Block 3', 15, 'MAINTENANCE', NULL),
                ('BK-203', 'SMART_BICYCLE', 'Campus Library South', 95, 'AVAILABLE', NULL);
            """)
            c.execute("""
                INSERT INTO reservations (reservation_id, vehicle_id, user_id, start_time, end_time, status) VALUES
                (1, 'BK-202', 'STU-1001', CURRENT_TIMESTAMP, NULL, 'ACTIVE');
            """)
        self._conn.commit()

    def cursor(self, dictionary=False):
        return SQLiteCursorAdapter(self._conn.cursor(), dictionary=dictionary)

    def commit(self):
        self._conn.commit()

    def is_connected(self):
        return True

    def ping(self, reconnect=True, attempts=3, delay=2):
        pass

def ensure_database_schema(conn):
    """
    Backup mechanism: Automatically creates database tables using
    database/schema.sql if required tables are not found.
    """
    schema_path = os.path.join(os.path.dirname(__file__), "database", "schema.sql")
    if not os.path.exists(schema_path):
        print(f"Warning: Schema file not found at {schema_path}")
        return

    try:
        cursor = conn.cursor()
        cursor.execute("SHOW TABLES")
        existing_tables = {row[0].lower() for row in cursor.fetchall()}
        cursor.close()

        required_tables = {"users", "vehicles", "reservations"}
        if not required_tables.issubset(existing_tables):
            missing = required_tables - existing_tables
            print(f"[Backup Mechanism] Missing table(s) detected: {missing}. Creating tables from {schema_path}...")

            with open(schema_path, "r", encoding="utf-8") as f:
                sql_content = f.read()

            statements = []
            current_stmt = []
            for line in sql_content.splitlines():
                trimmed = line.strip()
                if trimmed.startswith("--") or trimmed.startswith("/*"):
                    continue
                current_stmt.append(line)
                if trimmed.endswith(";"):
                    stmt = "\n".join(current_stmt).strip()
                    if stmt:
                        stmt_upper = stmt.upper()
                        # Skip database creation/switching because connection is already bound to DB_NAME
                        if not stmt_upper.startswith("CREATE DATABASE") and not stmt_upper.startswith("USE "):
                            statements.append(stmt)
                    current_stmt = []

            cur = conn.cursor()
            for statement in statements:
                cur.execute(statement)
            conn.commit()
            cur.close()
            print("[Backup Mechanism] All tables and initial seed data created successfully using schema.sql!")
    except Exception as e:
        print(f"[Backup Mechanism] Error while initializing database schema: {e}")

def connect_db():
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            port=DB_PORT,
            connection_timeout=2
        )
        ensure_database_schema(conn)
        print("Connected to MySQL database.")
        return conn
    except mysql.connector.Error as err:
        # If database itself doesn't exist (Error 1049), auto-create it
        if getattr(err, "errno", None) == 1049:
            try:
                print(f"[Backup Mechanism] Database '{DB_NAME}' not found. Auto-creating database...")
                admin_conn = mysql.connector.connect(
                    host=DB_HOST,
                    user=DB_USER,
                    password=DB_PASSWORD,
                    port=DB_PORT,
                    connection_timeout=2
                )
                admin_cursor = admin_conn.cursor()
                admin_cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                admin_cursor.close()
                admin_conn.close()

                conn = mysql.connector.connect(
                    host=DB_HOST,
                    user=DB_USER,
                    password=DB_PASSWORD,
                    database=DB_NAME,
                    port=DB_PORT
                )
                ensure_database_schema(conn)
                print(f"Connected to newly created MySQL database '{DB_NAME}'.")
                return conn
            except Exception as create_err:
                print(f"Could not auto-create database '{DB_NAME}': {create_err}")

        print(f"MySQL unavailable ({err}). Using local zero-setup SQLite database: campus_local.db")
        return SQLiteConnectionAdapter()
    except Exception as e:
        print(f"MySQL unavailable ({e}). Using local zero-setup SQLite database: campus_local.db")
        return SQLiteConnectionAdapter()

db = connect_db()

@app.before_request
def ensure_database_connection():
    global db

    try:
        if db is None or not db.is_connected():
            db = connect_db()
        else:
            db.ping(reconnect=True, attempts=2, delay=1)
    except Exception:
        db = connect_db()

@app.route("/")
def home():
    return render_template("index.html")


@app.route("/vehicles")
def get_vehicles():
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT * FROM vehicles")

    vehicles = cursor.fetchall()

    cursor.close()

    return jsonify(vehicles)

@app.route("/users")
def get_users():
    cursor = db.cursor(dictionary=True)

    cursor.execute("SELECT * FROM users")

    users = cursor.fetchall()

    cursor.close()

    return jsonify(users)

@app.route("/reservations")
def get_reservations():
    cursor = db.cursor(dictionary=True)

    query = """
        SELECT
            r.reservation_id,
            r.vehicle_id,
            v.vehicle_type,
            v.location,
            r.user_id,
            u.name,
            r.start_time,
            r.end_time,
            r.status
        FROM reservations r
        JOIN vehicles v ON r.vehicle_id = v.vehicle_id
        JOIN users u ON r.user_id = u.user_id
    """

    cursor.execute(query)

    reservations = cursor.fetchall()

    cursor.close()

    return jsonify(reservations)

@app.route("/vehicles", methods=["POST"])
def add_vehicle():
    data = request.get_json()

    vehicle_id = data.get("vehicle_id")
    vehicle_type = data.get("vehicle_type")
    location = data.get("location")
    battery_level = data.get("battery_level")
    status = data.get("status", "AVAILABLE")
    user_id = data.get("user_id")

    cursor = db.cursor()

    query = """
        INSERT INTO vehicles
        (vehicle_id, vehicle_type, location, battery_level, status, user_id)
        VALUES (%s, %s, %s, %s, %s, %s)
    """

    values = (
        vehicle_id,
        vehicle_type,
        location,
        battery_level,
        status,
        user_id
    )

    cursor.execute(query, values)
    db.commit()

    cursor.close()

    return jsonify({
        "message": "Vehicle registered successfully",
        "vehicle_id": vehicle_id
    }), 201

@app.route("/vehicles/<vehicle_id>", methods=["PUT"])
def update_vehicle(vehicle_id):
    data = request.get_json()

    location = data.get("location")
    battery_level = data.get("battery_level")
    status = data.get("status")
    user_id = data.get("user_id")

    cursor = db.cursor()

    query = """
        UPDATE vehicles
        SET location = %s,
            battery_level = %s,
            status = %s,
            user_id = %s,
            last_updated = CURRENT_TIMESTAMP
        WHERE vehicle_id = %s
    """

    values = (
        location,
        battery_level,
        status,
        user_id,
        vehicle_id
    )

    cursor.execute(query, values)
    db.commit()

    rows_updated = cursor.rowcount

    cursor.close()

    if rows_updated == 0:
        return jsonify({
            "message": "Vehicle not found"
        }), 404

    return jsonify({
        "message": "Vehicle updated successfully",
        "vehicle_id": vehicle_id
    })

@app.route("/vehicles/<vehicle_id>", methods=["DELETE"])
def delete_vehicle(vehicle_id):
    cursor = db.cursor()

    query = """
        DELETE FROM vehicles
        WHERE vehicle_id = %s
    """

    cursor.execute(query, (vehicle_id,))
    db.commit()

    rows_deleted = cursor.rowcount

    cursor.close()

    if rows_deleted == 0:
        return jsonify({
            "message": "Vehicle not found"
        }), 404

    return jsonify({
        "message": "Vehicle deleted successfully",
        "vehicle_id": vehicle_id
    })

@app.route("/reservations", methods=["POST"])
def create_reservation():
    data = request.get_json()

    vehicle_id = data.get("vehicle_id")
    user_id = data.get("user_id")

    cursor = db.cursor()

    # Check whether the vehicle exists and is available
    cursor.execute(
        "SELECT status FROM vehicles WHERE vehicle_id = %s",
        (vehicle_id,)
    )

    vehicle = cursor.fetchone()

    if vehicle is None:
        cursor.close()
        return jsonify({
            "message": "Vehicle not found"
        }), 404

    if vehicle[0] != "AVAILABLE":
        cursor.close()
        return jsonify({
            "message": "Vehicle is not available for reservation"
        }), 409

    # Create reservation
    cursor.execute(
        """
        INSERT INTO reservations
        (vehicle_id, user_id, status)
        VALUES (%s, %s, 'ACTIVE')
        """,
        (vehicle_id, user_id)
    )

    reservation_id = cursor.lastrowid

    # Change vehicle status
    cursor.execute(
        """
        UPDATE vehicles
        SET status = 'RESERVED',
            user_id = %s,
            last_updated = CURRENT_TIMESTAMP
        WHERE vehicle_id = %s
        """,
        (user_id, vehicle_id)
    )

    db.commit()
    cursor.close()

    return jsonify({
        "message": "Vehicle reserved successfully",
        "reservation_id": reservation_id,
        "vehicle_id": vehicle_id,
        "user_id": user_id
    }), 201

@app.route("/reservations/<int:reservation_id>/return", methods=["PUT"])
def return_vehicle(reservation_id):
    cursor = db.cursor(dictionary=True)

    # Find the active reservation
    cursor.execute(
        """
        SELECT vehicle_id, user_id
        FROM reservations
        WHERE reservation_id = %s
          AND status = 'ACTIVE'
        """,
        (reservation_id,)
    )

    reservation = cursor.fetchone()

    if reservation is None:
        cursor.close()
        return jsonify({
            "message": "Active reservation not found"
        }), 404

    vehicle_id = reservation["vehicle_id"]

    # Complete the reservation
    cursor.execute(
        """
        UPDATE reservations
        SET status = 'COMPLETED',
            end_time = CURRENT_TIMESTAMP
        WHERE reservation_id = %s
        """,
        (reservation_id,)
    )

    # Make vehicle available again
    cursor.execute(
        """
        UPDATE vehicles
        SET status = 'AVAILABLE',
            user_id = NULL,
            last_updated = CURRENT_TIMESTAMP
        WHERE vehicle_id = %s
        """,
        (vehicle_id,)
    )

    db.commit()

    cursor.close()

    return jsonify({
        "message": "Vehicle returned successfully",
        "reservation_id": reservation_id,
        "vehicle_id": vehicle_id
    })

@app.route("/vehicles/alerts")
def vehicle_alerts():
    cursor = db.cursor(dictionary=True)

    query = """
        SELECT
            vehicle_id,
            vehicle_type,
            location,
            battery_level,
            status,
            user_id
        FROM vehicles
        WHERE battery_level <= 20
           OR status = 'MAINTENANCE'
    """

    cursor.execute(query)

    alerts = cursor.fetchall()

    cursor.close()

    return jsonify({
        "alert_count": len(alerts),
        "vehicles": alerts
    })

if __name__ == "__main__":
    # Runs directly on port 5000 accessible via EC2 Public IP: http://<EC2-IP>:5000
    app.run(host="0.0.0.0", port=5000, debug=True)