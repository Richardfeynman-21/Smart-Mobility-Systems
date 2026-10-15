#!/usr/bin/env python3
"""
Database Viewer Utility for Smart Campus Mobility.
Supports both MySQL (AWS RDS / Local) and SQLite fallback anytime,
even when the Flask application is completely stopped.
"""

import os
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "smart_campus_mobility")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
SQLITE_PATH = os.getenv("SQLITE_PATH", os.path.join(os.path.dirname(__file__), "campus_local.db"))

def print_table(title, rows, columns):
    print(f"\n{'='*80}")
    print(f"  {title} ({len(rows)} rows)")
    print(f"{'='*80}")

    if not rows:
        print("  (Empty table)")
        return

    col_widths = [len(col) for col in columns]
    for row in rows:
        for idx, val in enumerate(row):
            col_widths[idx] = max(col_widths[idx], len(str(val) if val is not None else "NULL"))

    header = " | ".join(col.ljust(col_widths[idx]) for idx, col in enumerate(columns))
    print(f"  {header}")
    print(f"  {'-' * len(header)}")

    for row in rows:
        row_str = " | ".join(
            (str(val) if val is not None else "NULL").ljust(col_widths[idx])
            for idx, val in enumerate(row)
        )
        print(f"  {row_str}")

def fetch_and_display(cursor, db_type_str):
    print(f"\n>>> Source: {db_type_str}")

    # 1. Users
    try:
        cursor.execute("SELECT user_id, name, email, role FROM users")
        users = cursor.fetchall()
        print_table("TABLE: users", users, ["user_id", "name", "email", "role"])
    except Exception as e:
        print(f"Could not read users: {e}")

    # 2. Vehicles
    try:
        cursor.execute("SELECT vehicle_id, vehicle_type, location, battery_level, status, user_id FROM vehicles")
        vehicles = cursor.fetchall()
        print_table("TABLE: vehicles", vehicles, ["vehicle_id", "vehicle_type", "location", "battery", "status", "user_id"])
    except Exception as e:
        print(f"Could not read vehicles: {e}")

    # 3. Reservations & Waitlist
    try:
        try:
            cursor.execute("""
                SELECT reservation_id, vehicle_id, user_id,
                       COALESCE(booking_start, start_time) AS start_time,
                       booking_end, status
                FROM reservations ORDER BY reservation_id DESC
            """)
        except Exception:
            cursor.execute("SELECT reservation_id, vehicle_id, user_id, start_time, end_time, status FROM reservations ORDER BY reservation_id DESC")
        reservations = cursor.fetchall()
        print_table("TABLE: reservations", reservations, ["reservation_id", "vehicle_id", "user_id", "start_time", "end_time", "status"])
    except Exception as e:
        print(f"Could not read reservations: {e}")

    # 4. Maintenance
    try:
        cursor.execute("SELECT maintenance_id, vehicle_id, description, scheduled_start, scheduled_end, status FROM maintenance_records ORDER BY maintenance_id DESC")
        maint = cursor.fetchall()
        print_table("TABLE: maintenance_records", maint, ["id", "vehicle_id", "description", "start", "end", "status"])
    except Exception:
        pass

    # 5. Notifications
    try:
        cursor.execute("SELECT notification_id, user_id, message, notification_type, is_read FROM notifications ORDER BY notification_id DESC LIMIT 10")
        notifs = cursor.fetchall()
        print_table("TABLE: notifications (last 10)", notifs, ["id", "user_id", "message", "type", "read"])
    except Exception:
        pass

    # 6. Event Outbox
    try:
        cursor.execute("SELECT outbox_id, event_id, event_type, created_at, published_at FROM mobility_event_outbox ORDER BY outbox_id DESC LIMIT 10")
        outbox = cursor.fetchall()
        print_table("TABLE: mobility_event_outbox (last 10)", outbox, ["id", "event_id", "event_type", "created_at", "published_at"])
    except Exception:
        pass

def try_mysql():
    # 1. Try PyMySQL
    try:
        import pymysql
        conn = pymysql.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            port=DB_PORT,
            connect_timeout=5
        )
        cur = conn.cursor()
        fetch_and_display(cur, f"MySQL / RDS ({DB_HOST}:{DB_PORT} / {DB_NAME}) [via PyMySQL]")
        cur.close()
        conn.close()
        return True
    except Exception:
        pass

    # 2. Try mysql.connector
    try:
        import mysql.connector
        conn = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            port=DB_PORT,
            connection_timeout=5
        )
        cur = conn.cursor()
        fetch_and_display(cur, f"MySQL / RDS ({DB_HOST}:{DB_PORT} / {DB_NAME}) [via mysql-connector]")
        cur.close()
        conn.close()
        return True
    except Exception:
        pass

    return False

def try_sqlite():
    if not os.path.exists(SQLITE_PATH):
        print(f"\n[Info] Neither MySQL nor SQLite ('{SQLITE_PATH}') are available.")
        print("Please check your .env database settings or run the app to generate the database.")
        return False

    import sqlite3
    conn = sqlite3.connect(SQLITE_PATH)
    cur = conn.cursor()
    fetch_and_display(cur, f"Local SQLite database ({SQLITE_PATH})")
    cur.close()
    conn.close()
    return True

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--sqlite":
        try_sqlite()
        return

    if not try_mysql():
        print(f"\n[Notice] Could not connect to MySQL at {DB_HOST}:{DB_PORT}. Checking local SQLite...")
        try_sqlite()

if __name__ == "__main__":
    main()
