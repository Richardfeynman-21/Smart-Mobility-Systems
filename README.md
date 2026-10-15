# Smart Campus Mobility

A Flask-based campus fleet application for vehicle availability, reservations, waiting-list promotion, maintenance records, in-app notifications, analytics, and AWS event processing.

## Features

- Fleet dashboard with vehicle status, battery alerts, and automatic 15-second refresh.
- Reservations with requested start/end times and overlap checking.
- Automatic waiting list: conflicting requests are queued; cancellation/return checks the queue and confirms the earliest eligible request for an available slot.
- Maintenance scheduling, completion, and booking conflict checks.
- In-app notifications for confirmation, waiting-list status, cancellation, return, and maintenance events.
- Admin-style analytics summaries from reservation data.
- Request-scoped database connections and non-destructive schema migrations.
- Optional transactional outbox that publishes application events to DynamoDB.
- DynamoDB Streams -> Lambda event processing, processed-event deduplication, and CloudWatch Lambda error alarm via AWS SAM.

> The current student selector is a demonstration profile selector, not authentication. Do not expose this demo as a production system without implementing authentication and authorization for student/admin actions.

## Project layout

```text
app.py
requirements.txt
.env.example
database/schema.sql
static/css/style.css
static/js/script.js
templates/index.html
aws/template.yaml
aws/lambda/handler.py
deployment/smart-mobility.service.example
setup_ec2.sh
```

## Local setup

1. Create a virtual environment and install dependencies:

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

2. Configure the database:

   ```bash
   cp .env.example .env
   ```

   Edit `.env`. With `DB_HOST=localhost`, the application tries local MySQL first and uses the local SQLite development database if local MySQL is unavailable and `ALLOW_SQLITE_FALLBACK=true`. If `DB_HOST` points to a remote RDS endpoint, a failed MySQL connection returns an error instead of silently using a different SQLite database.

   If using a fresh local MySQL database, apply the safe bootstrap schema before launching:

   ```bash
   mysql -u root -p < database/schema.sql
   ```

3. Run:

   ```bash
   python app.py
   ```

   Open `http://127.0.0.1:5000`. The health endpoint is `/api/health`.

The application applies additive migrations on first connection. `database/schema.sql` is also non-destructive and does not drop existing tables.

## Reservation behavior

- Send `POST /reservations` with `vehicle_id`, `user_id`, `booking_start`, and `booking_end` (ISO date-time values). If the time fields are omitted, the API uses the current time and a one-hour duration for compatibility.
- An overlapping request is created with status `WAITLISTED` instead of failing. The API responds with status code `201` and `status: WAITLISTED`.
- Cancellation endpoint: `PUT /reservations/<reservation_id>/cancel`.
- Return endpoint: `PUT /reservations/<reservation_id>/return`.
- On cancellation or return, the system checks waiting requests in order and promotes the earliest request whose time slot has no conflict.
- A legacy reservation with no `booking_start` is treated as blocking until it is returned/cancelled. Return those old demo reservations before testing time-slot bookings for that vehicle.

Other useful endpoints: `GET /vehicles`, `/users`, `/reservations`, `/waitlist`, `/analytics`, `/maintenance`, `/notifications?user_id=STU-1001`, and `/vehicles/alerts`.

## Deploy to EC2 + RDS

1. Ensure the RDS database and user exist. Configure the RDS security group to allow port 3306 **only from the EC2 security group**.
2. Configure `.env` with the RDS endpoint, database name, username, and password. Set `ALLOW_SQLITE_FALLBACK=false` on EC2. Do not put `.env` into Git or share it.
3. Install requirements and initialize the schema safely:

   ```bash
   chmod +x setup_ec2.sh
   ./setup_ec2.sh
   ```

   The schema file no longer drops tables, but always take a database backup before applying schema changes to an existing database.
4. Start the app with Gunicorn:

   ```bash
   ./venv/bin/gunicorn --workers 2 --bind 0.0.0.0:5000 app:app
   ```

5. For automatic restarts after reboot, adapt `deployment/smart-mobility.service.example` to your actual EC2 username and application path, install it as a systemd unit, then enable/start it. Keep Flask debug mode disabled.
6. Configure inbound rules narrowly. For browser HTTPS, use a trusted domain/certificate and a TLS-capable deployment configuration. The Gunicorn command above serves HTTP on port 5000; it does not itself enable HTTPS.

## AWS rubric: DynamoDB, Streams, Lambda, CloudWatch

The Flask app keeps MySQL/RDS as the transactional source of truth. The outbox table records domain events in the same MySQL transaction as the related state change. When enabled, the application publishes those outbox records to DynamoDB.

1. Install/configure AWS SAM CLI and use an AWS account/region where you are authorized to create these resources.
2. From the `aws` directory, deploy the SAM template:

   ```bash
   sam build
   sam deploy --guided
   ```

   The template creates `smart-mobility-events` with DynamoDB Streams (`NEW_IMAGE`), `smart-mobility-processed-events` for idempotent processing, a Lambda event processor, and a CloudWatch alarm for Lambda errors. Review the estimated costs and generated IAM permissions before deploying.
3. Give the EC2 instance an IAM role that grants only `dynamodb:PutItem` on `smart-mobility-events`. Do not store AWS access keys in `.env`.
4. Add these settings to EC2 `.env` after successful deployment:

   ```dotenv
   AWS_EVENTS_ENABLED=true
   AWS_REGION=eu-north-1
   DYNAMODB_EVENTS_TABLE=smart-mobility-events
   ALLOW_SQLITE_FALLBACK=false
   ```

5. Restart the app service. Create/cancel a test reservation, then verify the event appears in DynamoDB, the stream invokes Lambda, the processed record appears in `smart-mobility-processed-events`, and Lambda logs appear in CloudWatch.

Lambda processes the stream for idempotent event audit/processing and emits structured logs. User-facing in-app notifications are created by Flask in MySQL. If DynamoDB is temporarily unavailable, the event remains pending in the MySQL outbox and is retried during a later publish attempt; monitor `mobility_event_outbox.last_error` and `attempts`.

## Testing checklist

- Health endpoint returns `{"status":"ok"}`.
- Book an available vehicle for a valid future/current time slot.
- Submit an overlapping request from a second user; it should become `WAITLISTED`.
- Cancel the confirmed booking; the eligible waiting-list request should become `CONFIRMED` and receive an in-app notification.
- Schedule maintenance; overlapping bookings should be rejected. Mark maintenance complete and verify status refreshes.
- Verify analytics, notifications, and alerts render after refresh.
- When AWS integration is enabled, verify DynamoDB item, stream-triggered Lambda log, processed event record, and CloudWatch alarm configuration.

Automated and AWS integration tests must be run in the target environment before treating the deployment as production-ready. Do not claim a successful AWS test until you have verified it in your account.
