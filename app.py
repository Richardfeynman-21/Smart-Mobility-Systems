"""Smart Campus Mobility Flask application.

MySQL/RDS is the transactional source of truth. Supports connection pooling,
multi-driver resilience (mysql-connector-python + PyMySQL fallback), and
zero-setup SQLite fallback for local development.
AWS event publishing is optional and uses a transactional outbox.
"""
import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import datetime, date, timedelta, timezone
from flask import Flask, g, jsonify, request, render_template
from flask.json.provider import DefaultJSONProvider
from werkzeug.local import LocalProxy

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    boto3 = None
    BotoCoreError = ClientError = Exception

class ISOJSONProvider(DefaultJSONProvider):
    def default(self, value):
        if isinstance(value, (datetime, date)):
            return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
        return super().default(value)

app = Flask(__name__)
app.json = ISOJSONProvider(app)
app.config["JSON_SORT_KEYS"] = False

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("smart_mobility")

# ==============================================================================
# Database & AWS Configuration
# ==============================================================================
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "smart_campus_mobility")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_TIMEOUT = int(os.getenv("DB_TIMEOUT", "10"))
DB_POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
DB_SSL_CA = os.getenv("DB_SSL_CA", None)
DB_SSL_DISABLED = os.getenv("DB_SSL_DISABLED", "false").lower() in ("true", "1", "yes")

SQLITE_PATH = os.getenv("SQLITE_PATH", os.path.join(os.path.dirname(__file__), "campus_local.db"))
ALLOW_SQLITE_FALLBACK = os.getenv("ALLOW_SQLITE_FALLBACK", "true").lower() in ("1", "true", "yes")

AWS_EVENTS_ENABLED = os.getenv("AWS_EVENTS_ENABLED", "false").lower() in ("1", "true", "yes")
DYNAMODB_EVENTS_TABLE = os.getenv("DYNAMODB_EVENTS_TABLE", "")
AWS_REGION = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION", "eu-north-1"))

_schema_lock = threading.Lock()
_schema_initialized = set()
_mysql_pool = None
_driver_type = None

# ==============================================================================
# Serialization & Row Normalization
# ==============================================================================
def _normalize_row(row, dictionary=False):
    if row is None:
        return None
    if isinstance(row, sqlite3.Row):
        d = dict(row)
        return d if dictionary else tuple(d.values())
    if isinstance(row, dict):
        if dictionary:
            return {k: (v.isoformat(sep=" ") if isinstance(v, (datetime, date)) else v) for k, v in row.items()}
        return tuple(row.values())
    if isinstance(row, (list, tuple)):
        clean = [v.isoformat(sep=" ") if isinstance(v, (datetime, date)) else v for v in row]
        return tuple(clean)
    return row

# ==============================================================================
# Cursor & Connection Adapters
# ==============================================================================
class CursorAdapter:
    """Unified cursor adapter supporting SQLite, MySQL Connector, and PyMySQL."""
    def __init__(self, cursor, mode="mysql", dictionary=False):
        self._cursor = cursor
        self._mode = mode
        self._dictionary = dictionary
        self.lastrowid = None
        self.rowcount = -1

    def execute(self, query, params=None):
        if self._mode == "sqlite":
            query = query.replace("%s", "?")
            params = tuple(
                value.isoformat(sep=" ") if isinstance(value, (datetime, date)) else value
                for value in (params or ())
            )
        self._cursor.execute(query, params or ())
        self.lastrowid = getattr(self._cursor, "lastrowid", None)
        self.rowcount = getattr(self._cursor, "rowcount", -1)
        return self

    def fetchone(self):
        return _normalize_row(self._cursor.fetchone(), self._dictionary)

    def fetchall(self):
        rows = self._cursor.fetchall() or []
        return [_normalize_row(row, self._dictionary) for row in rows]

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass

class ConnectionAdapter:
    """Unified connection adapter supporting commit, rollback, and cursor creation."""
    def __init__(self, raw_conn, mode="mysql"):
        self.raw = raw_conn
        self.mode = mode
        self.is_sqlite = (mode == "sqlite")

    def cursor(self, dictionary=False):
        if self.is_sqlite:
            return CursorAdapter(self.raw.cursor(), mode="sqlite", dictionary=dictionary)
        if self.mode == "pymysql":
            import pymysql.cursors
            cur_cls = pymysql.cursors.DictCursor if dictionary else pymysql.cursors.Cursor
            return CursorAdapter(self.raw.cursor(cur_cls), mode="pymysql", dictionary=dictionary)
        # mysql-connector: buffered=True prevents commands out of sync / packet order issues
        return CursorAdapter(self.raw.cursor(buffered=True, dictionary=dictionary), mode="mysql_connector", dictionary=dictionary)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        try:
            self.raw.rollback()
        except Exception:
            pass

    def close(self):
        try:
            self.raw.close()
        except Exception:
            pass

    def begin_immediate(self):
        if self.is_sqlite:
            self.raw.execute("BEGIN IMMEDIATE")

    def is_connected(self):
        if self.is_sqlite:
            return True
        if hasattr(self.raw, "is_connected"):
            try:
                return self.raw.is_connected()
            except Exception:
                return False
        if hasattr(self.raw, "ping"):
            try:
                self.raw.ping(reconnect=False)
                return True
            except Exception:
                return False
        return True

    def ping(self, reconnect=True):
        if hasattr(self.raw, "ping"):
            self.raw.ping(reconnect=reconnect)

# ==============================================================================
# Database Schema Initialization & Non-Destructive Migrations
# ==============================================================================
def _sqlite_schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE,
            role TEXT NOT NULL DEFAULT 'STUDENT', created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS vehicles (
            vehicle_id TEXT PRIMARY KEY, vehicle_type TEXT NOT NULL, location TEXT NOT NULL,
            battery_level INTEGER NOT NULL DEFAULT 100, status TEXT NOT NULL DEFAULT 'AVAILABLE',
            user_id TEXT, last_updated TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS reservations (
            reservation_id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id TEXT NOT NULL,
            user_id TEXT NOT NULL, start_time TEXT DEFAULT CURRENT_TIMESTAMP, end_time TEXT,
            status TEXT NOT NULL DEFAULT 'ACTIVE', booking_start TEXT, booking_end TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_reservation_vehicle_status ON reservations(vehicle_id, status);
        CREATE INDEX IF NOT EXISTS idx_reservation_user ON reservations(user_id);
        CREATE TABLE IF NOT EXISTS maintenance_records (
            maintenance_id INTEGER PRIMARY KEY AUTOINCREMENT, vehicle_id TEXT NOT NULL,
            description TEXT NOT NULL, scheduled_start TEXT, scheduled_end TEXT,
            status TEXT NOT NULL DEFAULT 'SCHEDULED', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT
        );
        CREATE TABLE IF NOT EXISTS notifications (
            notification_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
            message TEXT NOT NULL, notification_type TEXT NOT NULL DEFAULT 'INFO',
            related_reservation_id INTEGER, is_read INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS mobility_event_outbox (
            outbox_id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT UNIQUE NOT NULL,
            event_type TEXT NOT NULL, payload TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, published_at TEXT,
            attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT
        );
    """)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(reservations)").fetchall()}
    for column, definition in (("booking_start", "TEXT"), ("booking_end", "TEXT"), ("created_at", "TEXT")):
        if column not in existing:
            conn.execute(f"ALTER TABLE reservations ADD COLUMN {column} {definition}")
    conn.execute("UPDATE reservations SET created_at=COALESCE(created_at,start_time,CURRENT_TIMESTAMP) WHERE created_at IS NULL")
    conn.commit()
    _seed_sqlite(conn)

def _seed_sqlite(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        cur.executemany("INSERT INTO users (user_id,name,email,role) VALUES (?,?,?,?)", [
            ('STU-1001', 'Aarav Patel', 'aarav.patel@campus.edu', 'STUDENT'),
            ('STU-1002', 'Rishi Reddy', 'rishi.reddy@campus.edu', 'STUDENT'),
            ('STU-1003', 'Sneha Sharma', 'sneha.sharma@campus.edu', 'STUDENT'),
            ('STU-1004', 'Vikram Rao', 'vikram.rao@campus.edu', 'STUDENT'),
            ('FAC-2001', 'Dr. Meera Iyer', 'meera.iyer@campus.edu', 'FACULTY'),
        ])
    cur.execute("SELECT COUNT(*) FROM vehicles")
    if cur.fetchone()[0] == 0:
        cur.executemany("INSERT INTO vehicles (vehicle_id,vehicle_type,location,battery_level,status,user_id) VALUES (?,?,?,?,?,?)", [
            ('EV-101', 'ELECTRIC_SCOOTER', 'North Academic Gate', 85, 'AVAILABLE', None),
            ('EV-102', 'ELECTRIC_SCOOTER', 'Student Center Hub', 18, 'AVAILABLE', None),
            ('EV-103', 'ELECTRIC_CAR', 'Main Administration Plaza', 92, 'AVAILABLE', None),
            ('BK-201', 'SMART_BICYCLE', 'Engineering Block B', 100, 'AVAILABLE', None),
            ('BK-202', 'SMART_BICYCLE', 'Central Sports Complex', 100, 'AVAILABLE', None),
            ('EV-104', 'ELECTRIC_SCOOTER', 'Hostel Block 3', 15, 'MAINTENANCE', None),
            ('BK-203', 'SMART_BICYCLE', 'Campus Library South', 95, 'AVAILABLE', None),
        ])
    conn.commit()
    cur.close()

def _mysql_schema(conn):
    """Apply non-destructive, additive migrations to MySQL/RDS."""
    cur = conn.cursor()
    tables = [
        "CREATE TABLE IF NOT EXISTS users (user_id VARCHAR(50) NOT NULL PRIMARY KEY, name VARCHAR(100) NOT NULL, email VARCHAR(100) UNIQUE, role VARCHAR(20) NOT NULL DEFAULT 'STUDENT', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
        "CREATE TABLE IF NOT EXISTS vehicles (vehicle_id VARCHAR(50) NOT NULL PRIMARY KEY, vehicle_type VARCHAR(50) NOT NULL, location VARCHAR(100) NOT NULL, battery_level INT NOT NULL DEFAULT 100, status VARCHAR(20) NOT NULL DEFAULT 'AVAILABLE', user_id VARCHAR(50) NULL, last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, INDEX idx_vehicle_status (status), INDEX idx_vehicle_battery (battery_level), CONSTRAINT fk_vehicles_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL ON UPDATE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
        "CREATE TABLE IF NOT EXISTS reservations (reservation_id INT AUTO_INCREMENT PRIMARY KEY, vehicle_id VARCHAR(50) NOT NULL, user_id VARCHAR(50) NOT NULL, start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP, end_time TIMESTAMP NULL, booking_start DATETIME NULL, booking_end DATETIME NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE', INDEX idx_reservation_status (status), INDEX idx_reservation_user (user_id), INDEX idx_reservation_vehicle (vehicle_id), INDEX idx_reservation_vehicle_status (vehicle_id,status), CONSTRAINT fk_reservations_vehicle FOREIGN KEY (vehicle_id) REFERENCES vehicles(vehicle_id) ON DELETE CASCADE ON UPDATE CASCADE, CONSTRAINT fk_reservations_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE ON UPDATE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
        "CREATE TABLE IF NOT EXISTS maintenance_records (maintenance_id INT AUTO_INCREMENT PRIMARY KEY, vehicle_id VARCHAR(50) COLLATE utf8mb4_unicode_ci NOT NULL, description VARCHAR(500) NOT NULL, scheduled_start DATETIME NULL, scheduled_end DATETIME NULL, status VARCHAR(20) NOT NULL DEFAULT 'SCHEDULED', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, completed_at DATETIME NULL, INDEX idx_maintenance_vehicle_status (vehicle_id,status), CONSTRAINT fk_maintenance_vehicle FOREIGN KEY (vehicle_id) REFERENCES vehicles(vehicle_id) ON DELETE CASCADE ON UPDATE CASCADE) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
        "CREATE TABLE IF NOT EXISTS notifications (notification_id BIGINT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(50) NOT NULL, message VARCHAR(1000) NOT NULL, notification_type VARCHAR(40) NOT NULL DEFAULT 'INFO', related_reservation_id INT NULL, is_read TINYINT(1) NOT NULL DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, INDEX idx_notifications_user_read (user_id,is_read,created_at)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
        "CREATE TABLE IF NOT EXISTS mobility_event_outbox (outbox_id BIGINT AUTO_INCREMENT PRIMARY KEY, event_id VARCHAR(36) NOT NULL UNIQUE, event_type VARCHAR(80) NOT NULL, payload JSON NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, published_at DATETIME NULL, attempts INT NOT NULL DEFAULT 0, last_error TEXT NULL, INDEX idx_outbox_pending (published_at,outbox_id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci",
    ]
    for statement in tables:
        try:
            cur.execute(statement)
        except Exception as e:
            logger.debug(f"Table init notice: {e}")

    migrations = [
        "ALTER TABLE reservations ADD COLUMN booking_start DATETIME NULL",
        "ALTER TABLE reservations ADD COLUMN booking_end DATETIME NULL",
        "ALTER TABLE reservations ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE reservations MODIFY COLUMN status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'",
    ]
    for statement in migrations:
        try:
            cur.execute(statement)
        except Exception as exc:
            msg = str(exc).lower()
            if any(term in msg for term in ("duplicate column", "duplicate field", "already exists", "1060")):
                continue
            logger.debug(f"Migration notice: {exc}")

    # Seed initial demo data non-destructively
    seeds = [
        "INSERT IGNORE INTO users (user_id,name,email,role) VALUES ('STU-1001','Aarav Patel','aarav.patel@campus.edu','STUDENT'),('STU-1002','Rishi Reddy','rishi.reddy@campus.edu','STUDENT'),('STU-1003','Sneha Sharma','sneha.sharma@campus.edu','STUDENT'),('STU-1004','Vikram Rao','vikram.rao@campus.edu','STUDENT'),('FAC-2001','Dr. Meera Iyer','meera.iyer@campus.edu','FACULTY')",
        "INSERT IGNORE INTO vehicles (vehicle_id,vehicle_type,location,battery_level,status,user_id) VALUES ('EV-101','ELECTRIC_SCOOTER','North Academic Gate',85,'AVAILABLE',NULL),('EV-102','ELECTRIC_SCOOTER','Student Center Hub',18,'AVAILABLE',NULL),('EV-103','ELECTRIC_CAR','Main Administration Plaza',92,'AVAILABLE',NULL),('BK-201','SMART_BICYCLE','Engineering Block B',100,'AVAILABLE',NULL),('BK-202','SMART_BICYCLE','Central Sports Complex',100,'AVAILABLE',NULL),('EV-104','ELECTRIC_SCOOTER','Hostel Block 3',15,'MAINTENANCE',NULL),('BK-203','SMART_BICYCLE','Campus Library South',95,'AVAILABLE',NULL)"
    ]
    for seed in seeds:
        try:
            cur.execute(seed)
        except Exception as e:
            logger.debug(f"Seed notice: {e}")

    conn.commit()
    cur.close()

# ==============================================================================
# Connection Factory (Multi-Driver & Resilient Pooling)
# ==============================================================================
def _create_mysql_connection():
    global _mysql_pool, _driver_type

    config = {
        "host": DB_HOST,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "database": DB_NAME,
        "port": DB_PORT,
        "connection_timeout": DB_TIMEOUT,
        "autocommit": True,
        "charset": "utf8mb4",
    }
    if DB_SSL_CA and os.path.exists(DB_SSL_CA):
        config["ssl_ca"] = DB_SSL_CA
        config["ssl_verify_cert"] = True
    elif DB_SSL_DISABLED:
        config["ssl_disabled"] = True

    # 1. Try mysql.connector (with Connection Pooling)
    try:
        import mysql.connector
        from mysql.connector import pooling

        if _mysql_pool is None:
            try:
                _mysql_pool = pooling.MySQLConnectionPool(
                    pool_name="smart_mobility_pool",
                    pool_size=DB_POOL_SIZE,
                    pool_reset_session=True,
                    **config
                )
            except mysql.connector.Error as pool_err:
                # If database does not exist, create it
                if getattr(pool_err, "errno", None) == 1049:
                    admin_cfg = dict(config)
                    admin_cfg.pop("database", None)
                    admin_conn = mysql.connector.connect(**admin_cfg)
                    admin_cur = admin_conn.cursor()
                    admin_cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                    admin_cur.close()
                    admin_conn.close()
                    _mysql_pool = pooling.MySQLConnectionPool(
                        pool_name="smart_mobility_pool",
                        pool_size=DB_POOL_SIZE,
                        pool_reset_session=True,
                        **config
                    )
                else:
                    raise pool_err

        raw_conn = _mysql_pool.get_connection()
        conn = ConnectionAdapter(raw_conn, mode="mysql_connector")
        _driver_type = "mysql_connector"
        with _schema_lock:
            if "mysql" not in _schema_initialized:
                _mysql_schema(conn)
                _schema_initialized.add("mysql")
        return conn
    except Exception as mc_err:
        logger.debug(f"mysql.connector failed ({mc_err}); trying PyMySQL...")

    # 2. Try PyMySQL
    try:
        import pymysql

        ssl_ctx = None
        if DB_SSL_CA and os.path.exists(DB_SSL_CA):
            import ssl
            ssl_ctx = ssl.create_default_context(cafile=DB_SSL_CA)

        py_cfg = {
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
            raw_conn = pymysql.connect(**py_cfg)
        except pymysql.err.OperationalError as op_err:
            if op_err.args and op_err.args[0] == 1049:
                admin_cfg = dict(py_cfg)
                admin_cfg.pop("database", None)
                admin_conn = pymysql.connect(**admin_cfg)
                admin_cur = admin_conn.cursor()
                admin_cur.execute(f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
                admin_cur.close()
                admin_conn.close()
                raw_conn = pymysql.connect(**py_cfg)
            else:
                raise op_err

        conn = ConnectionAdapter(raw_conn, mode="pymysql")
        _driver_type = "pymysql"
        with _schema_lock:
            if "mysql" not in _schema_initialized:
                _mysql_schema(conn)
                _schema_initialized.add("mysql")
        return conn
    except Exception as py_err:
        logger.debug(f"PyMySQL failed ({py_err}).")
        raise

def _new_connection():
    configured_remote = DB_HOST not in ("localhost", "127.0.0.1", "")
    try:
        return _create_mysql_connection()
    except Exception as exc:
        if configured_remote or not ALLOW_SQLITE_FALLBACK:
            logger.exception("Unable to connect to configured MySQL database")
            raise RuntimeError(
                f"Database connection failed: {exc}. Check DB_HOST, credentials, and RDS security-group access."
            ) from exc

        if "sqlite" not in _schema_initialized:
            logger.warning("Local MySQL unavailable; using SQLite development database: %s", SQLITE_PATH)
        raw = sqlite3.connect(SQLITE_PATH, timeout=10, check_same_thread=False)
        raw.row_factory = sqlite3.Row
        conn = ConnectionAdapter(raw, mode="sqlite")
        with _schema_lock:
            if "sqlite" not in _schema_initialized:
                _sqlite_schema(raw)
                _schema_initialized.add("sqlite")
        return conn

def get_db():
    if "db" not in g:
        g.db = _new_connection()
    elif not g.db.is_connected():
        try:
            g.db.ping(reconnect=True)
        except Exception:
            g.db = _new_connection()
    return g.db

@app.teardown_appcontext
def close_db(_error=None):
    conn = g.pop("db", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            logger.exception("Error closing database connection")

# Thread-safe proxy for existing code
db = LocalProxy(get_db)

def _cursor(dictionary=False):
    return get_db().cursor(dictionary=dictionary)

# ==============================================================================
# Business Logic Helpers
# ==============================================================================
def _utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)

def _parse_time(value, default=None):
    if not value:
        return default
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise ValueError("Times must be valid ISO date-time values.")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed

def _queue_event(event_type, payload):
    conn = get_db()
    event_id = str(uuid.uuid4())
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO mobility_event_outbox (event_id,event_type,payload) VALUES (%s,%s,%s)",
        (event_id, event_type, json.dumps(payload, default=str))
    )
    cur.close()
    return event_id

def _publish_outbox(limit=20):
    if not AWS_EVENTS_ENABLED or not DYNAMODB_EVENTS_TABLE:
        return
    if boto3 is None:
        logger.error("AWS_EVENTS_ENABLED is true but boto3 is not installed")
        return
    conn = get_db()
    cur = conn.cursor(dictionary=True)
    cur.execute(
        "SELECT outbox_id,event_id,event_type,payload,created_at FROM mobility_event_outbox WHERE published_at IS NULL ORDER BY outbox_id LIMIT %s",
        (limit,)
    )
    rows = cur.fetchall()
    cur.close()
    if not rows:
        return
    table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(DYNAMODB_EVENTS_TABLE)
    for row in rows:
        try:
            payload = row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            try:
                table.put_item(
                    Item={
                        "event_id": str(row["event_id"]),
                        "event_type": str(row["event_type"]),
                        "payload": json.dumps(payload, default=str),
                        "created_at": str(row["created_at"]),
                    },
                    ConditionExpression="attribute_not_exists(event_id)"
                )
            except Exception as put_error:
                error_code = getattr(put_error, "response", {}).get("Error", {}).get("Code")
                if error_code != "ConditionalCheckFailedException":
                    raise
            mark = conn.cursor()
            mark.execute(
                "UPDATE mobility_event_outbox SET published_at=CURRENT_TIMESTAMP,last_error=NULL WHERE outbox_id=%s",
                (row["outbox_id"],)
            )
            conn.commit()
            mark.close()
        except Exception as exc:
            logger.exception("Could not publish event %s to DynamoDB", row["event_id"])
            mark = conn.cursor()
            mark.execute(
                "UPDATE mobility_event_outbox SET attempts=attempts+1,last_error=%s WHERE outbox_id=%s",
                (str(exc)[:1000], row["outbox_id"])
            )
            conn.commit()
            mark.close()

def _notify(user_id, message, notification_type="INFO", reservation_id=None):
    cur = _cursor()
    cur.execute(
        "INSERT INTO notifications (user_id,message,notification_type,related_reservation_id) VALUES (%s,%s,%s,%s)",
        (user_id, message, notification_type, reservation_id)
    )
    cur.close()

def _maintenance_conflict(vehicle_id, start, end):
    cur = _cursor()
    cur.execute("""
        SELECT maintenance_id FROM maintenance_records WHERE vehicle_id=%s AND status='SCHEDULED'
        AND (scheduled_start IS NULL OR (scheduled_start < %s AND (scheduled_end IS NULL OR scheduled_end > %s))) LIMIT 1
    """, (vehicle_id, end, start))
    found = cur.fetchone() is not None
    cur.close()
    return found

def _has_conflict(vehicle_id, start, end, exclude_reservation_id=None, lock=False):
    sql = """
        SELECT reservation_id FROM reservations WHERE vehicle_id=%s
        AND status IN ('ACTIVE','CONFIRMED')
        AND (booking_start IS NULL OR (booking_start < %s AND booking_end > %s))
    """
    params = [vehicle_id, end, start]
    if exclude_reservation_id is not None:
        sql += " AND reservation_id<>%s"
        params.append(exclude_reservation_id)
    if lock and not get_db().is_sqlite:
        sql += " FOR UPDATE"
    cur = _cursor()
    cur.execute(sql, tuple(params))
    found = cur.fetchone() is not None
    cur.close()
    return found

def _promote_waitlist(vehicle_id):
    """Promote the oldest eligible waitlisted request whose requested slot is free."""
    cur = _cursor(dictionary=True)
    cur.execute(
        "SELECT reservation_id,user_id,booking_start,booking_end FROM reservations WHERE vehicle_id=%s AND status='WAITLISTED' ORDER BY created_at,reservation_id",
        (vehicle_id,)
    )
    waiting = cur.fetchall()
    cur.close()
    for item in waiting:
        start = _parse_time(item["booking_start"])
        end = _parse_time(item["booking_end"])
        if start is None or end is None:
            continue
        if _has_conflict(vehicle_id, start, end, exclude_reservation_id=item["reservation_id"], lock=True):
            continue
        cur = _cursor()
        cur.execute(
            "UPDATE reservations SET status='CONFIRMED' WHERE reservation_id=%s AND status='WAITLISTED'",
            (item["reservation_id"],)
        )
        changed = cur.rowcount
        cur.close()
        if changed:
            _notify(
                item["user_id"],
                f"Your waiting-list request for vehicle {vehicle_id} has been confirmed.",
                "WAITLIST_PROMOTED",
                item["reservation_id"]
            )
            _queue_event("WaitlistPromoted", {
                "reservation_id": item["reservation_id"],
                "vehicle_id": vehicle_id,
                "user_id": item["user_id"]
            })
            break

def _refresh_vehicle_status(vehicle_id):
    """Recalculate the visible current status from maintenance and time slots."""
    now = _utcnow()
    cur = _cursor()
    cur.execute("SELECT status FROM vehicles WHERE vehicle_id=%s", (vehicle_id,))
    row = cur.fetchone()
    if row is None:
        cur.close()
        return

    cur.execute("""
        SELECT maintenance_id FROM maintenance_records WHERE vehicle_id=%s AND status='SCHEDULED'
        AND (scheduled_start IS NULL OR scheduled_start<=%s)
        AND (scheduled_end IS NULL OR scheduled_end>%s) LIMIT 1
    """, (vehicle_id, now, now))
    maintenance_now = cur.fetchone() is not None
    if maintenance_now:
        cur.execute(
            "UPDATE vehicles SET status='MAINTENANCE',user_id=NULL,last_updated=CURRENT_TIMESTAMP WHERE vehicle_id=%s",
            (vehicle_id,)
        )
        cur.close()
        return

    if row[0] == "MAINTENANCE":
        cur.execute(
            "UPDATE vehicles SET status='AVAILABLE',user_id=NULL,last_updated=CURRENT_TIMESTAMP WHERE vehicle_id=%s",
            (vehicle_id,)
        )

    cur.execute("""
        SELECT user_id FROM reservations WHERE vehicle_id=%s AND status IN ('ACTIVE','CONFIRMED')
        AND booking_start<=%s AND booking_end>%s ORDER BY booking_start LIMIT 1
    """, (vehicle_id, now, now))
    active = cur.fetchone()
    if active:
        cur.execute(
            "UPDATE vehicles SET status='RESERVED',user_id=%s,last_updated=CURRENT_TIMESTAMP WHERE vehicle_id=%s",
            (active[0], vehicle_id)
        )
    else:
        cur.execute(
            "SELECT user_id FROM reservations WHERE vehicle_id=%s AND status='ACTIVE' AND booking_start IS NULL LIMIT 1",
            (vehicle_id,)
        )
        legacy = cur.fetchone()
        if legacy:
            cur.execute(
                "UPDATE vehicles SET status='RESERVED',user_id=%s,last_updated=CURRENT_TIMESTAMP WHERE vehicle_id=%s",
                (legacy[0], vehicle_id)
            )
        else:
            cur.execute(
                "UPDATE vehicles SET status='AVAILABLE',user_id=NULL,last_updated=CURRENT_TIMESTAMP WHERE vehicle_id=%s",
                (vehicle_id,)
            )
    cur.close()

# ==============================================================================
# Error Handlers
# ==============================================================================
@app.errorhandler(RuntimeError)
def handle_runtime_error(error):
    logger.error("Application runtime error: %s", error)
    return jsonify({"message": str(error)}), 503

@app.errorhandler(500)
def handle_internal_error(error):
    logger.exception("Unhandled application error: %s", error)
    return jsonify({"message": "An unexpected server error occurred."}), 500

# ==============================================================================
# Application Routes
# ==============================================================================
@app.route("/api/health")
@app.route("/health")
def health():
    cur = _cursor()
    cur.execute("SELECT 1")
    cur.fetchone()
    cur.close()
    backend = "sqlite" if get_db().is_sqlite else (_driver_type or "mysql")
    return jsonify({
        "status": "ok",
        "database": "connected",
        "backend": backend,
        "aws_events_enabled": AWS_EVENTS_ENABLED
    })

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/vehicles", methods=["GET"])
def get_vehicles():
    ids = _cursor()
    ids.execute("SELECT vehicle_id FROM vehicles")
    vehicle_ids = [row[0] for row in ids.fetchall()]
    ids.close()
    for vehicle_id in vehicle_ids:
        _refresh_vehicle_status(vehicle_id)
    get_db().commit()
    cur = _cursor(dictionary=True)
    cur.execute("SELECT * FROM vehicles ORDER BY vehicle_id")
    vehicles = cur.fetchall()
    cur.close()
    return jsonify(vehicles)

@app.route("/users", methods=["GET"])
def get_users():
    cur = _cursor(dictionary=True)
    cur.execute("SELECT * FROM users ORDER BY name")
    users = cur.fetchall()
    cur.close()
    return jsonify(users)

@app.route("/reservations", methods=["GET"])
def get_reservations():
    cur = _cursor(dictionary=True)
    cur.execute("""
        SELECT r.reservation_id,r.vehicle_id,v.vehicle_type,v.location,r.user_id,u.name,
               r.start_time,r.end_time,r.booking_start,r.booking_end,r.status,r.created_at
        FROM reservations r JOIN vehicles v ON r.vehicle_id=v.vehicle_id
        JOIN users u ON r.user_id=u.user_id ORDER BY r.created_at DESC,r.reservation_id DESC
    """)
    rows = cur.fetchall()
    cur.close()
    return jsonify(rows)

@app.route("/waitlist", methods=["GET"])
def get_waitlist():
    user_id = request.args.get("user_id")
    cur = _cursor(dictionary=True)
    sql = """
        SELECT r.reservation_id,r.vehicle_id,v.vehicle_type,r.user_id,u.name,r.booking_start,r.booking_end,r.created_at
        FROM reservations r JOIN vehicles v ON r.vehicle_id=v.vehicle_id
        JOIN users u ON r.user_id=u.user_id WHERE r.status='WAITLISTED'
    """
    params = ()
    if user_id:
        sql += " AND r.user_id=%s"
        params = (user_id,)
    sql += " ORDER BY r.created_at,r.reservation_id"
    cur.execute(sql, params)
    rows = cur.fetchall()
    cur.close()
    return jsonify(rows)

@app.route("/analytics", methods=["GET"])
def analytics():
    cur = _cursor(dictionary=True)
    cur.execute("SELECT status,COUNT(*) AS count FROM reservations GROUP BY status")
    by_status = cur.fetchall()
    cur.execute("""
        SELECT vehicle_id,COUNT(*) AS booking_count FROM reservations
        WHERE status<>'CANCELLED' GROUP BY vehicle_id ORDER BY booking_count DESC,vehicle_id LIMIT 10
    """)
    popular = cur.fetchall()
    cur.execute("SELECT COUNT(*) AS total FROM reservations")
    total = cur.fetchone()["total"]
    cur.execute("SELECT COUNT(*) AS total, SUM(CASE WHEN status='RESERVED' THEN 1 ELSE 0 END) AS reserved FROM vehicles")
    vehicle_summary = cur.fetchone()
    vehicle_total = vehicle_summary["total"]
    reserved_total = vehicle_summary["reserved"] or 0
    cur.execute("SELECT COUNT(*) AS total FROM reservations WHERE status='WAITLISTED'")
    waiting = cur.fetchone()["total"]
    cur.close()
    utilization = round((reserved_total / vehicle_total) * 100, 1) if vehicle_total else 0
    return jsonify({
        "reservation_total": total,
        "vehicle_total": vehicle_total,
        "waitlist_total": waiting,
        "current_utilization_percent": utilization,
        "reservations_by_status": by_status,
        "popular_vehicles": popular
    })

@app.route("/maintenance", methods=["GET", "POST"])
def maintenance():
    if request.method == "GET":
        cur = _cursor(dictionary=True)
        cur.execute("""
            SELECT m.*,v.vehicle_type,v.location FROM maintenance_records m
            JOIN vehicles v ON m.vehicle_id=v.vehicle_id ORDER BY m.created_at DESC
        """)
        rows = cur.fetchall()
        cur.close()
        return jsonify(rows)

    data = request.get_json(silent=True) or {}
    vehicle_id = str(data.get("vehicle_id", "")).strip()
    description = str(data.get("description", "")).strip()
    if not vehicle_id or not description:
        return jsonify({"message": "vehicle_id and description are required."}), 400

    start = data.get("scheduled_start") or _utcnow().isoformat(timespec="minutes")
    end = data.get("scheduled_end")
    try:
        parsed_start = _parse_time(start)
        parsed_end = _parse_time(end) if end else None
        if parsed_end and parsed_end <= parsed_start:
            return jsonify({"message": "Maintenance end must be after its start."}), 400
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400

    conn = get_db()
    if conn.is_sqlite:
        conn.begin_immediate()
    cur = _cursor()
    cur.execute("SELECT vehicle_id FROM vehicles WHERE vehicle_id=%s", (vehicle_id,))
    if cur.fetchone() is None:
        cur.close()
        conn.rollback()
        return jsonify({"message": "Vehicle not found."}), 404

    if _has_conflict(vehicle_id, parsed_start, parsed_end or (parsed_start + timedelta(days=3650)), lock=True):
        cur.close()
        conn.rollback()
        return jsonify({
            "message": "Maintenance overlaps an existing confirmed reservation. Reschedule it or resolve the reservation first."
        }), 409

    cur.execute(
        "INSERT INTO maintenance_records (vehicle_id,description,scheduled_start,scheduled_end,status) VALUES (%s,%s,%s,%s,'SCHEDULED')",
        (vehicle_id, description, parsed_start, parsed_end)
    )
    maintenance_id = cur.lastrowid
    if parsed_start <= _utcnow():
        cur.execute(
            "UPDATE vehicles SET status='MAINTENANCE',user_id=NULL,last_updated=CURRENT_TIMESTAMP WHERE vehicle_id=%s",
            (vehicle_id,)
        )
    _queue_event("MaintenanceScheduled", {
        "maintenance_id": maintenance_id,
        "vehicle_id": vehicle_id,
        "description": description
    })
    conn.commit()
    cur.close()
    _publish_outbox()
    return jsonify({"message": "Maintenance scheduled.", "maintenance_id": maintenance_id}), 201

@app.route("/maintenance/<int:maintenance_id>/complete", methods=["PUT"])
def complete_maintenance(maintenance_id):
    conn = get_db()
    cur = _cursor(dictionary=True)
    cur.execute(
        "SELECT vehicle_id FROM maintenance_records WHERE maintenance_id=%s AND status='SCHEDULED'",
        (maintenance_id,)
    )
    record = cur.fetchone()
    if not record:
        cur.close()
        return jsonify({"message": "Scheduled maintenance record not found."}), 404

    cur.execute(
        "UPDATE maintenance_records SET status='COMPLETED',completed_at=CURRENT_TIMESTAMP WHERE maintenance_id=%s",
        (maintenance_id,)
    )
    cur.execute(
        "SELECT COUNT(*) AS count FROM maintenance_records WHERE vehicle_id=%s AND status='SCHEDULED'",
        (record["vehicle_id"],)
    )
    remaining = cur.fetchone()["count"]
    if remaining == 0:
        reset = _cursor()
        reset.execute(
            "UPDATE vehicles SET status='AVAILABLE',user_id=NULL,last_updated=CURRENT_TIMESTAMP WHERE vehicle_id=%s AND status='MAINTENANCE'",
            (record["vehicle_id"],)
        )
        reset.close()
        _refresh_vehicle_status(record["vehicle_id"])

    _queue_event("MaintenanceCompleted", {
        "maintenance_id": maintenance_id,
        "vehicle_id": record["vehicle_id"]
    })
    conn.commit()
    cur.close()
    _publish_outbox()
    return jsonify({"message": "Maintenance marked complete."})

@app.route("/notifications", methods=["GET"])
def notifications():
    user_id = request.args.get("user_id")
    if not user_id:
        return jsonify({"message": "user_id is required."}), 400
    cur = _cursor(dictionary=True)
    cur.execute("""
        SELECT notification_id,user_id,message,notification_type,related_reservation_id,is_read,created_at
        FROM notifications WHERE user_id=%s ORDER BY created_at DESC,notification_id DESC LIMIT 100
    """, (user_id,))
    rows = cur.fetchall()
    cur.close()
    return jsonify(rows)

@app.route("/notifications/<int:notification_id>/read", methods=["PUT"])
def mark_notification_read(notification_id):
    user_id = (request.get_json(silent=True) or {}).get("user_id")
    if not user_id:
        return jsonify({"message": "user_id is required."}), 400
    cur = _cursor()
    cur.execute("UPDATE notifications SET is_read=1 WHERE notification_id=%s AND user_id=%s", (notification_id, user_id))
    updated = cur.rowcount
    cur.close()
    if not updated:
        return jsonify({"message": "Notification not found."}), 404
    get_db().commit()
    return jsonify({"message": "Notification marked as read."})

@app.route("/vehicles", methods=["POST"])
def add_vehicle():
    data = request.get_json(silent=True) or {}
    required = ("vehicle_id", "vehicle_type", "location")
    if any(not str(data.get(key, "")).strip() for key in required):
        return jsonify({"message": "vehicle_id, vehicle_type, and location are required."}), 400
    try:
        battery = int(data.get("battery_level", 100))
        if not 0 <= battery <= 100:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"message": "battery_level must be between 0 and 100."}), 400

    cur = _cursor()
    cur.execute(
        "INSERT INTO vehicles (vehicle_id,vehicle_type,location,battery_level,status,user_id) VALUES (%s,%s,%s,%s,'AVAILABLE',NULL)",
        (str(data["vehicle_id"]).strip(), str(data["vehicle_type"]).strip(), str(data["location"]).strip(), battery)
    )
    cur.close()
    _queue_event("VehicleCreated", {"vehicle_id": data["vehicle_id"]})
    get_db().commit()
    _publish_outbox()
    return jsonify({"message": "Vehicle registered successfully", "vehicle_id": data["vehicle_id"]}), 201

@app.route("/vehicles/<vehicle_id>", methods=["PUT"])
def update_vehicle(vehicle_id):
    data = request.get_json(silent=True) or {}
    fields, values = [], []
    for key in ("location", "battery_level", "status", "user_id"):
        if key in data:
            fields.append({"location": "location", "battery_level": "battery_level", "status": "status", "user_id": "user_id"}[key] + "=%s")
            values.append(data[key])
    if not fields:
        return jsonify({"message": "At least one vehicle field must be supplied."}), 400
    if "battery_level" in data:
        try:
            if not 0 <= int(data["battery_level"]) <= 100:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"message": "battery_level must be between 0 and 100."}), 400
    fields.append("last_updated=CURRENT_TIMESTAMP")
    values.append(vehicle_id)
    cur = _cursor()
    cur.execute("UPDATE vehicles SET " + ",".join(fields) + " WHERE vehicle_id=%s", tuple(values))
    updated = cur.rowcount
    cur.close()
    if not updated:
        return jsonify({"message": "Vehicle not found."}), 404
    _queue_event("VehicleUpdated", {"vehicle_id": vehicle_id, "fields": list(data.keys())})
    get_db().commit()
    _publish_outbox()
    return jsonify({"message": "Vehicle updated successfully", "vehicle_id": vehicle_id})

@app.route("/vehicles/<vehicle_id>", methods=["DELETE"])
def delete_vehicle(vehicle_id):
    cur = _cursor()
    cur.execute(
        "SELECT COUNT(*) FROM reservations WHERE vehicle_id=%s AND status IN ('ACTIVE','CONFIRMED','WAITLISTED')",
        (vehicle_id,)
    )
    if cur.fetchone()[0]:
        cur.close()
        return jsonify({"message": "Cannot delete a vehicle with active or waitlisted reservations."}), 409
    cur.execute("DELETE FROM vehicles WHERE vehicle_id=%s", (vehicle_id,))
    deleted = cur.rowcount
    cur.close()
    if not deleted:
        return jsonify({"message": "Vehicle not found."}), 404
    _queue_event("VehicleDeleted", {"vehicle_id": vehicle_id})
    get_db().commit()
    _publish_outbox()
    return jsonify({"message": "Vehicle deleted successfully", "vehicle_id": vehicle_id})

@app.route("/reservations", methods=["POST"])
def create_reservation():
    data = request.get_json(silent=True) or {}
    vehicle_id = str(data.get("vehicle_id", "")).strip()
    user_id = str(data.get("user_id", "")).strip()
    if not vehicle_id or not user_id:
        return jsonify({"message": "vehicle_id and user_id are required."}), 400
    now = _utcnow().replace(microsecond=0)
    try:
        start = _parse_time(data.get("booking_start"), now)
        end = _parse_time(data.get("booking_end"), start + timedelta(hours=1))
        if end <= start:
            return jsonify({"message": "Booking end must be after booking start."}), 400
        if start < now - timedelta(minutes=1):
            return jsonify({"message": "Booking start cannot be in the past."}), 400
    except ValueError as exc:
        return jsonify({"message": str(exc)}), 400

    conn = get_db()
    if conn.is_sqlite:
        conn.begin_immediate()
    cur = _cursor(dictionary=True)
    lock_suffix = "" if conn.is_sqlite else " FOR UPDATE"
    cur.execute("SELECT vehicle_id,status FROM vehicles WHERE vehicle_id=%s" + lock_suffix, (vehicle_id,))
    vehicle = cur.fetchone()
    if not vehicle:
        cur.close()
        conn.rollback()
        return jsonify({"message": "Vehicle not found."}), 404

    if _maintenance_conflict(vehicle_id, start, end):
        cur.close()
        conn.rollback()
        return jsonify({
            "message": "Booking rejected: this vehicle is scheduled for maintenance during the selected time slot."
        }), 409

    cur.execute("SELECT user_id FROM users WHERE user_id=%s", (user_id,))
    if cur.fetchone() is None:
        cur.close()
        conn.rollback()
        return jsonify({"message": "User not found."}), 404

    conflict = _has_conflict(vehicle_id, start, end, lock=True)
    status = "WAITLISTED" if conflict else "CONFIRMED"
    cur.execute(
        "INSERT INTO reservations (vehicle_id,user_id,start_time,booking_start,booking_end,status) VALUES (%s,%s,CURRENT_TIMESTAMP,%s,%s,%s)",
        (vehicle_id, user_id, start, end, status)
    )
    reservation_id = cur.lastrowid
    if not conflict:
        _notify(user_id, f"Your reservation for vehicle {vehicle_id} is confirmed.", "BOOKING_CONFIRMED", reservation_id)
        _queue_event("ReservationConfirmed", {
            "reservation_id": reservation_id,
            "vehicle_id": vehicle_id,
            "user_id": user_id,
            "booking_start": start,
            "booking_end": end
        })
    else:
        _notify(user_id, f"Vehicle {vehicle_id} is already booked for that time. You are on the waiting list.", "WAITLISTED", reservation_id)
        _queue_event("ReservationWaitlisted", {
            "reservation_id": reservation_id,
            "vehicle_id": vehicle_id,
            "user_id": user_id,
            "booking_start": start,
            "booking_end": end
        })
    cur.close()
    _refresh_vehicle_status(vehicle_id)
    conn.commit()
    _publish_outbox()
    if conflict:
        return jsonify({
            "message": "Vehicle is booked for that time. You have been added to the waiting list.",
            "reservation_id": reservation_id,
            "status": status,
            "vehicle_id": vehicle_id,
            "user_id": user_id
        }), 201
    return jsonify({
        "message": "Reservation confirmed.",
        "reservation_id": reservation_id,
        "status": status,
        "vehicle_id": vehicle_id,
        "user_id": user_id
    }), 201

@app.route("/reservations/<int:reservation_id>/cancel", methods=["PUT"])
def cancel_reservation(reservation_id):
    conn = get_db()
    if conn.is_sqlite:
        conn.begin_immediate()
    cur = _cursor(dictionary=True)
    lock_suffix = "" if conn.is_sqlite else " FOR UPDATE"
    cur.execute("SELECT vehicle_id,user_id,status FROM reservations WHERE reservation_id=%s" + lock_suffix, (reservation_id,))
    row = cur.fetchone()
    if not row or row["status"] not in ("ACTIVE", "CONFIRMED", "WAITLISTED"):
        cur.close()
        conn.rollback()
        return jsonify({"message": "An active or waitlisted reservation was not found."}), 404
    vehicle_id, user_id = row["vehicle_id"], row["user_id"]
    cur.execute("UPDATE reservations SET status='CANCELLED',end_time=CURRENT_TIMESTAMP WHERE reservation_id=%s", (reservation_id,))
    _notify(user_id, f"Your reservation request for vehicle {vehicle_id} has been cancelled.", "BOOKING_CANCELLED", reservation_id)
    _queue_event("ReservationCancelled", {"reservation_id": reservation_id, "vehicle_id": vehicle_id, "user_id": user_id})
    cur.close()
    _promote_waitlist(vehicle_id)
    _refresh_vehicle_status(vehicle_id)
    conn.commit()
    _publish_outbox()
    return jsonify({"message": "Reservation cancelled. Eligible waiting-list requests have been processed."})

@app.route("/reservations/<int:reservation_id>/return", methods=["PUT"])
def return_vehicle(reservation_id):
    conn = get_db()
    if conn.is_sqlite:
        conn.begin_immediate()
    cur = _cursor(dictionary=True)
    lock_suffix = "" if conn.is_sqlite else " FOR UPDATE"
    cur.execute("SELECT vehicle_id,user_id,status FROM reservations WHERE reservation_id=%s" + lock_suffix, (reservation_id,))
    row = cur.fetchone()
    if not row or row["status"] not in ("ACTIVE", "CONFIRMED"):
        cur.close()
        conn.rollback()
        return jsonify({"message": "Active reservation not found."}), 404
    vehicle_id, user_id = row["vehicle_id"], row["user_id"]
    cur.execute("UPDATE reservations SET status='COMPLETED',end_time=CURRENT_TIMESTAMP WHERE reservation_id=%s", (reservation_id,))
    _notify(user_id, f"Vehicle {vehicle_id} has been returned successfully.", "BOOKING_COMPLETED", reservation_id)
    _queue_event("ReservationCompleted", {"reservation_id": reservation_id, "vehicle_id": vehicle_id, "user_id": user_id})
    cur.close()
    _promote_waitlist(vehicle_id)
    _refresh_vehicle_status(vehicle_id)
    conn.commit()
    _publish_outbox()
    return jsonify({"message": "Vehicle returned successfully", "reservation_id": reservation_id, "vehicle_id": vehicle_id})

@app.route("/vehicles/alerts")
def vehicle_alerts():
    cur = _cursor(dictionary=True)
    cur.execute("""
        SELECT vehicle_id,vehicle_type,location,battery_level,status,user_id
        FROM vehicles WHERE battery_level<=20 OR status='MAINTENANCE' ORDER BY vehicle_id
    """)
    rows = cur.fetchall()
    cur.close()
    return jsonify({"alert_count": len(rows), "vehicles": rows})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)