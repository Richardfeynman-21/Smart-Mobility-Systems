import os
from flask import Flask, jsonify, request, render_template
import mysql.connector

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

app = Flask(__name__)

# MySQL database configuration
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "smart_campus_mobility")
DB_PORT = int(os.getenv("DB_PORT", "3306"))

def connect_db():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        port=DB_PORT
    )

try:
    db = connect_db()
except Exception as e:
    print(f"Warning: Initial database connection failed: {e}")
    db = None

@app.before_request
def ensure_database_connection():
    global db

    try:
        if db is None or not db.is_connected():
            db = connect_db()
        else:
            db.ping(
                reconnect=True,
                attempts=3,
                delay=2
            )
    except (mysql.connector.Error, Exception):
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

    reservation_id = cursor.lastrowid

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