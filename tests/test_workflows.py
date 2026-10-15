"""Local API regression tests; run with `python -m unittest discover -s tests`."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta

_TEST_DIR = tempfile.TemporaryDirectory(prefix="smart-mobility-tests-")
os.environ["DB_HOST"] = "localhost"
os.environ["ALLOW_SQLITE_FALLBACK"] = "true"
os.environ["SQLITE_PATH"] = os.path.join(_TEST_DIR.name, "test.sqlite")
os.environ["AWS_EVENTS_ENABLED"] = "false"

import app as mobility_app  # noqa: E402


class MobilityWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        mobility_app.app.config.update(TESTING=True)
        cls.client = mobility_app.app.test_client()
        # Initialize schema once, then clear mutable records before each test.
        cls.client.get("/api/health")

    def setUp(self):
        conn = mobility_app.sqlite3.connect(os.environ["SQLITE_PATH"])
        conn.execute("DELETE FROM reservations")
        conn.execute("DELETE FROM notifications")
        conn.execute("DELETE FROM maintenance_records")
        conn.execute("DELETE FROM mobility_event_outbox")
        conn.execute("UPDATE vehicles SET status='AVAILABLE',user_id=NULL")
        conn.commit()
        conn.close()

    def slot(self, days=2):
        start = (datetime.now() + timedelta(days=days)).replace(microsecond=0)
        return start, start + timedelta(hours=1)

    def test_health_and_dashboard(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.get("/vehicles").status_code, 200)
        self.assertEqual(self.client.get("/analytics").status_code, 200)

    def test_conflicting_request_is_promoted_after_cancellation(self):
        start, end = self.slot()
        first = self.client.post("/reservations", json={
            "vehicle_id": "EV-101", "user_id": "STU-1001",
            "booking_start": start.isoformat(), "booking_end": end.isoformat(),
        })
        self.assertEqual(first.status_code, 201, first.get_json())
        self.assertEqual(first.get_json()["status"], "CONFIRMED")

        second = self.client.post("/reservations", json={
            "vehicle_id": "EV-101", "user_id": "STU-1002",
            "booking_start": start.isoformat(), "booking_end": end.isoformat(),
        })
        self.assertEqual(second.status_code, 201, second.get_json())
        self.assertEqual(second.get_json()["status"], "WAITLISTED")

        cancelled = self.client.put(f"/reservations/{first.get_json()['reservation_id']}/cancel")
        self.assertEqual(cancelled.status_code, 200, cancelled.get_json())
        rows = self.client.get("/reservations").get_json()
        promoted = next(row for row in rows if row["reservation_id"] == second.get_json()["reservation_id"])
        self.assertEqual(promoted["status"], "CONFIRMED")
        self.assertTrue(self.client.get("/notifications?user_id=STU-1002").get_json())

    def test_maintenance_blocks_overlapping_booking(self):
        start, end = self.slot()
        created = self.client.post("/maintenance", json={
            "vehicle_id": "EV-103", "description": "Scheduled inspection",
            "scheduled_start": start.isoformat(), "scheduled_end": end.isoformat(),
        })
        self.assertEqual(created.status_code, 201, created.get_json())
        booking = self.client.post("/reservations", json={
            "vehicle_id": "EV-103", "user_id": "STU-1001",
            "booking_start": start.isoformat(), "booking_end": end.isoformat(),
        })
        self.assertEqual(booking.status_code, 409, booking.get_json())
        completed = self.client.put(f"/maintenance/{created.get_json()['maintenance_id']}/complete")
        self.assertEqual(completed.status_code, 200, completed.get_json())

    def test_maintenance_rejected_when_confirmed_booking_overlaps(self):
        start, end = self.slot()
        booking = self.client.post("/reservations", json={
            "vehicle_id": "EV-102", "user_id": "STU-1001",
            "booking_start": start.isoformat(), "booking_end": end.isoformat(),
        })
        self.assertEqual(booking.status_code, 201, booking.get_json())
        self.assertEqual(booking.get_json()["status"], "CONFIRMED")

        maintenance = self.client.post("/maintenance", json={
            "vehicle_id": "EV-102", "description": "Conflicting inspection",
            "scheduled_start": start.isoformat(), "scheduled_end": end.isoformat(),
        })
        self.assertEqual(maintenance.status_code, 409, maintenance.get_json())

    def test_invalid_slot_is_rejected(self):
        start, end = self.slot()
        response = self.client.post("/reservations", json={
            "vehicle_id": "EV-101", "user_id": "STU-1001",
            "booking_start": end.isoformat(), "booking_end": start.isoformat(),
        })
        self.assertEqual(response.status_code, 400)

    def test_missing_user_and_vehicle_are_rejected(self):
        start, end = self.slot()
        response = self.client.post("/reservations", json={
            "vehicle_id": "NOT-A-VEHICLE", "user_id": "STU-1001",
            "booking_start": start.isoformat(), "booking_end": end.isoformat(),
        })
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
