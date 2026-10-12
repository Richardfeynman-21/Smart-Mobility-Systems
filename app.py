import os
import sys
import logging
import sqlite3
from datetime import datetime, date
from flask import Flask, jsonify, request, render_template, g
from werkzeug.local import LocalProxy

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("SmartCampusMobility")

app = Flask(__name__)

# ==============================================================================
# Database Configuration (AWS RDS MySQL / Local MySQL / SQLite Fallback)
# ==============================================================================
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "smart_campus_mobility")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_SSL_CA = os.getenv("DB_SSL_CA", None)
DB_SSL_DISABLED = os.getenv("DB_SSL_DISABLED", "false").lower() in ("true", "1", "yes")
DB_TIMEOUT = int(os.getenv("DB_TIMEOUT", "10"))
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))

# Global references for driver & pool management
_mysql_pool = None
_driver_type = None  # 'mysql_connector', 'pymysql', or 'sqlite'
_schema_initialized = False

# ==============================================================================
# Helper Serialization for JSON responses
# ==============================================================================
def serialize_row(row):
    """Ensure datetime, date, and decimal fields are JSON serializable."""
    if not isinstance(row, dict):
        return row
    clean = {}
    for k, v in row.items():
        if isinstance(v, (datetime, date)):
            clean[k] = v.isoformat()
        else:
            clean[k] = v
    return clean

# ==============================================================================
# SQLite Adapters (Zero-setup fallback)
# ==============================================================================
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
        try:
            self.cursor.close()
        except Exception:
            pass

class SQLiteConnectionAdapter:
    def __init__(self, db_path="campus_local.db"):
        self.db_path = db_path
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._backend_type = "sqlite"

    def cursor(self, dictionary=False):
        return SQLiteCursorAdapter(self._conn.cursor(), dictionary=dictionary)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def is_connected(self):
        return True

    def ping(self, reconnect=True, attempts=1, delay=0):
        pass

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

# ==============================================================================
# PyMySQL Adapters
# ==============================================================================
class PyMySQLCursorAdapter:
    def __init__(self, cursor, dictionary=False):
        self.cursor = cursor
        self.dictionary = dictionary

    @property
    def lastrowid(self):
        return self.cursor.lastrowid

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def execute(self, query, params=None):
        if params is not None:
            self.cursor.execute(query, params)
        else:
            self.cursor.execute(query)
        return self

    def fetchall(self):
        rows = self.cursor.fetchall()
        if self.dictionary and rows:
            return [serialize_row(r) for r in rows]
        return rows

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is not None and self.dictionary:
            return serialize_row(row)
        return row

    def close(self):
        try:
            self.cursor.close()
        except Exception:
            pass

class PyMySQLConnectionAdapter:
    def __init__(self, raw_conn):
        self._conn = raw_conn
        self._backend_type = "pymysql"

    def cursor(self, dictionary=False):
        import pymysql.cursors
        cur_cls = pymysql.cursors.DictCursor if dictionary else pymysql.cursors.Cursor
        raw_cur = self._conn.cursor(cur_cls)
        return PyMySQLCursorAdapter(raw_cur, dictionary=dictionary)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def is_connected(self):
        try:
            self._conn.ping(reconnect=False)
            return True
        except Exception:
            return False

    def ping(self, reconnect=True, attempts=1, delay=0):
        try:
            self._conn.ping(reconnect=reconnect)
        except Exception as e:
            logger.warning(f"PyMySQL ping failed: {e}")

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

# ==============================================================================
# MySQL Connector Adapters
# ==============================================================================
class MySQLConnectorCursorAdapter:
    def __init__(self, cursor, dictionary=False):
        self.cursor = cursor
        self.dictionary = dictionary

    @property
    def lastrowid(self):
        return self.cursor.lastrowid

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def execute(self, query, params=None):
        if params is not None:
            self.cursor.execute(query, params)
        else:
            self.cursor.execute(query)
        return self

    def fetchall(self):
        rows = self.cursor.fetchall()
        if self.dictionary and rows:
            return [serialize_row(r) for r in rows]
        return rows

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is not None and self.dictionary:
            return serialize_row(row)
        return row

    def close(self):
        try:
            self.cursor.close()
        except Exception:
            pass

class MySQLConnectorConnectionAdapter:
    def __init__(self, raw_conn):
        self._conn = raw_conn
        self._backend_type = "mysql_connector"

    def cursor(self, dictionary=False):
        # Using buffered=True guarantees no "Unread result found" conflicts
        raw_cur = self._conn.cursor(buffered=True, dictionary=dictionary)
        return MySQLConnectorCursorAdapter(raw_cur, dictionary=dictionary)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        try:
            self._conn.rollback()
        except Exception:
            pass

    def is_connected(self):
        try:
            return self._conn.is_connected()
        except Exception:
            return False

    def ping(self, reconnect=True, attempts=2, delay=1):
        try:
            self._conn.ping(reconnect=reconnect, attempts=attempts, delay=delay)
        except Exception as e:
            logger.warning(f"MySQL Connector ping failed: {e}")

    def close(self):
        try:
            self._conn.close()
        except Exception:
            pass

# ==============================================================================
# Schema Initialization & Verification
# ==============================================================================
def ensure_database_schema(conn):
    """
    Ensures required tables and initial seed data exist in the database.
    Uses CREATE TABLE IF NOT EXISTS and INSERT IGNORE to prevent conflicts.
    """
    global _schema_initialized
    if _schema_initialized:
        return

    schema_path = os.path.join(os.path.dirname(__file__), "database", "schema.sql")
    if not os.path.exists(schema_path):
        logger.warning(f"Schema file not found at {schema_path}")
        return

    try:
        cur = conn.cursor()
        cur.execute("SHOW TABLES")
        tables = [row[0].lower() for row in cur.fetchall()]
        cur.close()

        required = {"users", "vehicles", "reservations"}
        if not required.issubset(set(tables)):
            logger.info(f"Missing table(s) in {DB_NAME}: {required - set(tables)}. Initializing schema...")
            with open(schema_path, "r", encoding="utf-8") as f:
                sql_content = f.read()

            statements = []
            current = []
            for line in sql_content.splitlines():
                trimmed = line.strip()
                if trimmed.startswith("--") or trimmed.startswith("/*"):
                    continue
                current.append(line)
                if trimmed.endswith(";"):
                    stmt = "\n".join(current).strip()
                    if stmt:
                        stmt_upper = stmt.upper()
                        # Skip database creation/USE statements as connection is already bound
                        if not stmt_upper.startswith("CREATE DATABASE") and not stmt_upper.startswith("USE "):
                            statements.append(stmt)
                    current = []

            exec_cur = conn.cursor()
            for statement in statements:
                try:
                    exec_cur.execute(statement)
                except Exception as stmt_err:
                    logger.debug(f"Statement executed with notice: {stmt_err}")
            conn.commit()
            exec_cur.close()
            logger.info("Database schema and seed data verified successfully.")
        _schema_initialized = True
    except Exception as e:
        logger.error(f"Error checking/initializing schema: {e}")

def init_sqlite_schema(conn):
    """Initializes tables and initial seed data for SQLite fallback."""
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                email TEXT UNIQUE,
                role TEXT DEFAULT 'STUDENT',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
        cur.execute("""
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
        cur.execute("""
            CREATE TABLE IF NOT EXISTS reservations (
                reservation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                vehicle_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_time TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'ACTIVE'
            );
        """)
        cur.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] == 0:
            cur.execute("""
                INSERT OR IGNORE INTO users (user_id, name, email, role) VALUES
                ('STU-1001', 'Jithesh', 'aarav.patel@campus.edu', 'STUDENT'),
                ('STU-1002', 'Rishi Reddy', 'rishi.reddy@campus.edu', 'STUDENT'),
                ('STU-1003', 'Sneha Sharma', 'sneha.sharma@campus.edu', 'STUDENT'),
                ('STU-1004', 'Vikram Rao', 'vikram.rao@campus.edu', 'STUDENT'),
                ('FAC-2001', 'Dr. Meera Iyer', 'meera.iyer@campus.edu', 'FACULTY');
            """)
            cur.execute("""
                INSERT OR IGNORE INTO vehicles (vehicle_id, vehicle_type, location, battery_level, status, user_id) VALUES
                ('EV-101', 'ELECTRIC_SCOOTER', 'North Academic Gate', 85, 'AVAILABLE', NULL),
                ('EV-102', 'ELECTRIC_SCOOTER', 'Student Center Hub', 18, 'AVAILABLE', NULL),
                ('EV-103', 'ELECTRIC_CAR', 'Main Administration Plaza', 92, 'AVAILABLE', NULL),
                ('BK-201', 'SMART_BICYCLE', 'Engineering Block B', 100, 'AVAILABLE', NULL),
                ('BK-202', 'SMART_BICYCLE', 'Central Sports Complex', 100, 'RESERVED', 'STU-1001'),
                ('EV-104', 'ELECTRIC_SCOOTER', 'Hostel Block 3', 15, 'MAINTENANCE', NULL),
                ('BK-203', 'SMART_BICYCLE', 'Campus Library South', 95, 'AVAILABLE', NULL);
            """)
            cur.execute("""
                INSERT OR IGNORE INTO reservations (reservation_id, vehicle_id, user_id, start_time, end_time, status) VALUES
                (1, 'BK-202', 'STU-1001', CURRENT_TIMESTAMP, NULL, 'ACTIVE');
            """)
        conn.commit()
        cur.close()
    except Exception as e:
        logger.error(f"Error initializing SQLite schema: {e}")

# ==============================================================================
# Connection Factory (Multi-Driver: mysql-connector -> PyMySQL -> SQLite)
# ==============================================================================
def create_mysql_connector_connection():
    """Attempts connection using mysql-connector-python with pooling support."""
    global _mysql_pool
    import mysql.connector
    from mysql.connector import pooling

    config = {
        "host": DB_HOST,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "database": DB_NAME,
        "port": DB_PORT,
        "connection_timeout": DB_TIMEOUT,
        "autocommit": True,
        "charset": "utf8mb4",
        "collation": "utf8mb4_unicode_ci",
    }

    if DB_SSL_CA and os.path.exists(DB_SSL_CA):
        config["ssl_ca"] = DB_SSL_CA
        config["ssl_verify_cert"] = True
    elif DB_SSL_DISABLED:
        config["ssl_disabled"] = True

    # Initialize connection pool if not already created
    if _mysql_pool is None:
        try:
            _mysql_pool = pooling.MySQLConnectionPool(
                pool_name="campus_mobility_pool",
                pool_size=DB_POOL_SIZE,
                pool_reset_session=True,
                **config
            )
            logger.info(f"MySQL connection pool initialized (size={DB_POOL_SIZE}).")
        except mysql.connector.Error as pool_err:
            # If database doesn't exist (Error 1049), auto-create database first
            if getattr(pool_err, "errno", None) == 1049:
                logger.info(f"Database '{DB_NAME}' not found. Creating database on MySQL/RDS...")
                admin_cfg = dict(config)
                admin_cfg.pop("database", None)
                admin_conn = mysql.connector.connect(**admin_cfg)
                admin_cur = admin_conn.cursor()
                admin_cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                admin_cur.close()
                admin_conn.close()

                _mysql_pool = pooling.MySQLConnectionPool(
                    pool_name="campus_mobility_pool",
                    pool_size=DB_POOL_SIZE,
                    pool_reset_session=True,
                    **config
                )
            else:
                raise pool_err

    raw_conn = _mysql_pool.get_connection()
    adapter = MySQLConnectorConnectionAdapter(raw_conn)
    ensure_database_schema(adapter)
    return adapter

def create_pymysql_connection():
    """Attempts connection using pure-Python PyMySQL with SSL & caching_sha2 support."""
    import pymysql

    ssl_ctx = None
    if DB_SSL_CA and os.path.exists(DB_SSL_CA):
        import ssl
        ssl_ctx = ssl.create_default_context(cafile=DB_SSL_CA)

    config = {
        "host": DB_HOST,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "database": DB_NAME,
        "port": DB_PORT,
        "connect_timeout": DB_TIMEOUT,
        "autocommit": True,
        "charset": "utf8mb4",
        "ssl": ssl_ctx,
    }

    try:
        raw_conn = pymysql.connect(**config)
    except pymysql.err.OperationalError as op_err:
        # Check if database doesn't exist (Error 1049)
        if op_err.args and op_err.args[0] == 1049:
            logger.info(f"Database '{DB_NAME}' not found. Creating database on MySQL/RDS...")
            admin_cfg = dict(config)
            admin_cfg.pop("database", None)
            admin_conn = pymysql.connect(**admin_cfg)
            admin_cur = admin_conn.cursor()
            admin_cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            admin_cur.close()
            admin_conn.close()
            raw_conn = pymysql.connect(**config)
        else:
            raise op_err

    adapter = PyMySQLConnectionAdapter(raw_conn)
    ensure_database_schema(adapter)
    return adapter

def create_connection():
    """
    Main resilient connection factory:
    1. Tries mysql.connector (with connection pooling)
    2. Tries PyMySQL (pure-Python fallback)
    3. Tries local SQLite (campus_local.db)
    """
    global _driver_type

    # 1. Try mysql-connector
    try:
        conn = create_mysql_connector_connection()
        _driver_type = "mysql_connector"
        return conn
    except Exception as e_mc:
        logger.debug(f"mysql-connector connection failed: {e_mc}. Trying PyMySQL...")

    # 2. Try PyMySQL
    try:
        conn = create_pymysql_connection()
        _driver_type = "pymysql"
        return conn
    except Exception as e_py:
        logger.debug(f"PyMySQL connection failed: {e_py}. Falling back to SQLite...")

    # 3. Fallback to SQLite
    _driver_type = "sqlite"
    logger.warning("MySQL/RDS unavailable. Using local zero-setup SQLite database: campus_local.db")
    adapter = SQLiteConnectionAdapter()
    init_sqlite_schema(adapter)
    return adapter

# ==============================================================================
# Per-Request Database Lifecycle (Flask Context)
# ==============================================================================
def get_db():
    """Retrieves or creates a thread-safe connection for the current request."""
    if "db" not in g:
        g.db = create_connection()
    elif not getattr(g.db, "is_connected", lambda: True)():
        try:
            g.db.ping(reconnect=True)
        except Exception:
            g.db = create_connection()
    return g.db

@app.teardown_appcontext
def teardown_db(exception=None):
    """Safely returns or closes the database connection at the end of each request."""
    db_conn = g.pop("db", None)
    if db_conn is not None:
        try:
            db_conn.close()
        except Exception:
            pass

# Thread-safe proxy allowing existing route syntax 'db.cursor()', 'db.commit()'
db = LocalProxy(get_db)

# ==============================================================================
# Global Error Handlers (Prevents Server Crashes)
# ==============================================================================
@app.errorhandler(500)
def handle_500_error(e):
    logger.error(f"Internal 500 Error: {e}")
    return jsonify({"error": "Internal Server Error", "message": str(e)}), 500

@app.errorhandler(Exception)
def handle_unexpected_error(e):
    logger.exception(f"Unhandled exception in request: {e}")
    return jsonify({"error": "Unexpected Error", "message": str(e)}), 500

# ==============================================================================
# Application Routes
# ==============================================================================
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/health")
def health_check():
    """Health check endpoint for AWS EC2/RDS monitoring and load balancers."""
    cur = None
    try:
        cur = db.cursor()
        cur.execute("SELECT 1")
        cur.fetchall()
        backend = getattr(db, "_backend_type", _driver_type or "unknown")
        return jsonify({
            "status": "healthy",
            "database": "connected",
            "backend": backend,
            "host": DB_HOST if backend != "sqlite" else "campus_local.db",
            "database_name": DB_NAME if backend != "sqlite" else "sqlite"
        }), 200
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "host": DB_HOST
        }), 503
    finally:
        if cur:
            cur.close()

@app.route("/vehicles", methods=["GET"])
def get_vehicles():
    cur = None
    try:
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT * FROM vehicles")
        vehicles = cur.fetchall()
        return jsonify(vehicles)
    except Exception as e:
        logger.error(f"Error fetching vehicles: {e}")
        return jsonify({"error": "Failed to fetch vehicles", "details": str(e)}), 500
    finally:
        if cur:
            cur.close()

@app.route("/users", methods=["GET"])
def get_users():
    cur = None
    try:
        cur = db.cursor(dictionary=True)
        cur.execute("SELECT * FROM users")
        users = cur.fetchall()
        return jsonify(users)
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        return jsonify({"error": "Failed to fetch users", "details": str(e)}), 500
    finally:
        if cur:
            cur.close()

@app.route("/reservations", methods=["GET"])
def get_reservations():
    cur = None
    try:
        cur = db.cursor(dictionary=True)
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
        cur.execute(query)
        reservations = cur.fetchall()
        return jsonify(reservations)
    except Exception as e:
        logger.error(f"Error fetching reservations: {e}")
        return jsonify({"error": "Failed to fetch reservations", "details": str(e)}), 500
    finally:
        if cur:
            cur.close()

@app.route("/vehicles", methods=["POST"])
def add_vehicle():
    data = request.get_json() or {}
    vehicle_id = data.get("vehicle_id")
    vehicle_type = data.get("vehicle_type")
    location = data.get("location")
    battery_level = data.get("battery_level", 100)
    status = data.get("status", "AVAILABLE")
    user_id = data.get("user_id")

    if not vehicle_id or not vehicle_type or not location:
        return jsonify({"error": "vehicle_id, vehicle_type, and location are required"}), 400

    cur = None
    try:
        cur = db.cursor()
        query = """
            INSERT INTO vehicles
            (vehicle_id, vehicle_type, location, battery_level, status, user_id)
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        cur.execute(query, (vehicle_id, vehicle_type, location, battery_level, status, user_id))
        db.commit()
        return jsonify({
            "message": "Vehicle registered successfully",
            "vehicle_id": vehicle_id
        }), 201
    except Exception as e:
        db.rollback()
        logger.error(f"Error registering vehicle {vehicle_id}: {e}")
        return jsonify({"error": "Failed to register vehicle", "details": str(e)}), 500
    finally:
        if cur:
            cur.close()

@app.route("/vehicles/<vehicle_id>", methods=["PUT"])
def update_vehicle(vehicle_id):
    data = request.get_json() or {}
    location = data.get("location")
    battery_level = data.get("battery_level")
    status = data.get("status")
    user_id = data.get("user_id")

    cur = None
    try:
        cur = db.cursor()
        query = """
            UPDATE vehicles
            SET location = %s,
                battery_level = %s,
                status = %s,
                user_id = %s,
                last_updated = CURRENT_TIMESTAMP
            WHERE vehicle_id = %s
        """
        cur.execute(query, (location, battery_level, status, user_id, vehicle_id))
        db.commit()
        rows_updated = cur.rowcount
        if rows_updated == 0:
            return jsonify({"message": "Vehicle not found"}), 404
        return jsonify({
            "message": "Vehicle updated successfully",
            "vehicle_id": vehicle_id
        })
    except Exception as e:
        db.rollback()
        logger.error(f"Error updating vehicle {vehicle_id}: {e}")
        return jsonify({"error": "Failed to update vehicle", "details": str(e)}), 500
    finally:
        if cur:
            cur.close()

@app.route("/vehicles/<vehicle_id>", methods=["DELETE"])
def delete_vehicle(vehicle_id):
    cur = None
    try:
        cur = db.cursor()
        query = "DELETE FROM vehicles WHERE vehicle_id = %s"
        cur.execute(query, (vehicle_id,))
        db.commit()
        rows_deleted = cur.rowcount
        if rows_deleted == 0:
            return jsonify({"message": "Vehicle not found"}), 404
        return jsonify({
            "message": "Vehicle deleted successfully",
            "vehicle_id": vehicle_id
        })
    except Exception as e:
        db.rollback()
        logger.error(f"Error deleting vehicle {vehicle_id}: {e}")
        return jsonify({"error": "Failed to delete vehicle", "details": str(e)}), 500
    finally:
        if cur:
            cur.close()

@app.route("/reservations", methods=["POST"])
def create_reservation():
    data = request.get_json() or {}
    vehicle_id = data.get("vehicle_id")
    user_id = data.get("user_id")

    if not vehicle_id or not user_id:
        return jsonify({"error": "vehicle_id and user_id are required"}), 400

    cur = None
    try:
        cur = db.cursor()
        # Verify vehicle status
        cur.execute("SELECT status FROM vehicles WHERE vehicle_id = %s", (vehicle_id,))
        vehicle = cur.fetchone()

        if vehicle is None:
            return jsonify({"message": "Vehicle not found"}), 404

        v_status = vehicle[0] if isinstance(vehicle, (list, tuple)) else vehicle.get("status")
        if v_status != "AVAILABLE":
            return jsonify({"message": "Vehicle is not available for reservation"}), 409

        # Create reservation
        cur.execute(
            "INSERT INTO reservations (vehicle_id, user_id, status) VALUES (%s, %s, 'ACTIVE')",
            (vehicle_id, user_id)
        )
        reservation_id = cur.lastrowid

        # Update vehicle status
        cur.execute(
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

        return jsonify({
            "message": "Vehicle reserved successfully",
            "reservation_id": reservation_id,
            "vehicle_id": vehicle_id,
            "user_id": user_id
        }), 201
    except Exception as e:
        db.rollback()
        logger.error(f"Error creating reservation for {vehicle_id}: {e}")
        return jsonify({"error": "Failed to create reservation", "details": str(e)}), 500
    finally:
        if cur:
            cur.close()

@app.route("/reservations/<int:reservation_id>/return", methods=["PUT"])
def return_vehicle(reservation_id):
    cur = None
    try:
        cur = db.cursor(dictionary=True)
        cur.execute(
            """
            SELECT vehicle_id, user_id
            FROM reservations
            WHERE reservation_id = %s
              AND status = 'ACTIVE'
            """,
            (reservation_id,)
        )
        reservation = cur.fetchone()

        if reservation is None:
            return jsonify({"message": "Active reservation not found"}), 404

        vehicle_id = reservation["vehicle_id"]

        # Complete reservation
        cur.execute(
            """
            UPDATE reservations
            SET status = 'COMPLETED',
                end_time = CURRENT_TIMESTAMP
            WHERE reservation_id = %s
            """,
            (reservation_id,)
        )

        # Mark vehicle AVAILABLE
        cur.execute(
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

        return jsonify({
            "message": "Vehicle returned successfully",
            "reservation_id": reservation_id,
            "vehicle_id": vehicle_id
        })
    except Exception as e:
        db.rollback()
        logger.error(f"Error returning reservation {reservation_id}: {e}")
        return jsonify({"error": "Failed to return vehicle", "details": str(e)}), 500
    finally:
        if cur:
            cur.close()

@app.route("/vehicles/alerts", methods=["GET"])
def vehicle_alerts():
    cur = None
    try:
        cur = db.cursor(dictionary=True)
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
        cur.execute(query)
        alerts = cur.fetchall()
        return jsonify({
            "alert_count": len(alerts),
            "vehicles": alerts
        })
    except Exception as e:
        logger.error(f"Error fetching vehicle alerts: {e}")
        return jsonify({"error": "Failed to fetch vehicle alerts", "details": str(e)}), 500
    finally:
        if cur:
            cur.close()

if __name__ == "__main__":
    # Runs directly on port 5000 accessible via EC2 Public IP: http://<EC2-IP>:5000
    app.run(host="0.0.0.0", port=5000, debug=False)