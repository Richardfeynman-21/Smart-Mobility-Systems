#!/usr/bin/env python3
"""
Quick utility script to view the local SQLite database anytime,
even when the Flask application is completely stopped.
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "campus_local.db")

def print_table(title, rows, columns):
    print(f"\n{'='*70}")
    print(f"  {title} ({len(rows)} rows)")
    print(f"{'='*70}")

    if not rows:
        print("  (Empty table)")
        return

    # Calculate column widths
    col_widths = [len(col) for col in columns]
    for row in rows:
        for idx, val in enumerate(row):
            col_widths[idx] = max(col_widths[idx], len(str(val) if val is not None else "NULL"))

    # Header
    header = " | ".join(col.ljust(col_widths[idx]) for idx, col in enumerate(columns))
    print(f"  {header}")
    print(f"  {'-' * len(header)}")

    # Rows
    for row in rows:
        row_str = " | ".join(
            (str(val) if val is not None else "NULL").ljust(col_widths[idx])
            for idx, val in enumerate(row)
        )
        print(f"  {row_str}")

def main():
    if not os.path.exists(DB_PATH):
        print(f"\n[Info] '{DB_PATH}' has not been created yet.")
        print("Run the app once ('./venv/bin/python3 app.py') to generate and populate the database.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. Users
    cursor.execute("SELECT user_id, name, email, role FROM users")
    users = cursor.fetchall()
    print_table("TABLE: users", users, ["user_id", "name", "email", "role"])

    # 2. Vehicles
    cursor.execute("SELECT vehicle_id, vehicle_type, location, battery_level, status, user_id FROM vehicles")
    vehicles = cursor.fetchall()
    print_table("TABLE: vehicles", vehicles, ["vehicle_id", "vehicle_type", "location", "battery", "status", "user_id"])

    # 3. Reservations
    cursor.execute("SELECT reservation_id, vehicle_id, user_id, start_time, end_time, status FROM reservations")
    reservations = cursor.fetchall()
    print_table("TABLE: reservations", reservations, ["reservation_id", "vehicle_id", "user_id", "start_time", "end_time", "status"])

    conn.close()
    print(f"\nDatabase file location: {DB_PATH}\n")

if __name__ == "__main__":
    main()
