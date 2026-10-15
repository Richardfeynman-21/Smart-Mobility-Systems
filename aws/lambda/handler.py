"""Idempotent DynamoDB Streams consumer for Smart Campus Mobility events.

The Flask app remains responsible for transactional booking logic and in-app
notifications in MySQL. This Lambda validates/audits published domain events
and records each processed stream record once in a separate DynamoDB table.
"""
import json
import logging
import os
import time
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(os.getenv("LOG_LEVEL", "INFO"))


def _decode(value):
    if isinstance(value, dict):
        return {key: _decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def lambda_handler(event, context):
    table_name = os.environ["PROCESSED_EVENTS_TABLE"]
    table = boto3.resource("dynamodb").Table(table_name)
    processed = 0
    duplicates = 0
    failures = []

    for record in event.get("Records", []):
        if record.get("eventSource") != "aws:dynamodb" or record.get("eventName") not in ("INSERT", "MODIFY"):
            continue
        image = record.get("dynamodb", {}).get("NewImage") or {}
        app_event_id = image.get("event_id", {}).get("S")
        event_type = image.get("event_type", {}).get("S", "UnknownEvent")
        raw_payload = image.get("payload", {}).get("S", "{}")
        stream_id = record.get("eventID")
        if not stream_id:
            logger.warning("Skipping stream record without eventID")
            continue
        try:
            payload = json.loads(raw_payload)
        except (TypeError, ValueError):
            payload = {"unparsed_payload": raw_payload}

        item = {
            "event_id": stream_id,
            "application_event_id": app_event_id or "unknown",
            "event_type": event_type,
            "payload_summary": json.dumps(_decode(payload), separators=(",", ":"))[:3500],
            "processed_at": int(time.time()),
            "expires_at": int(time.time()) + 30 * 24 * 60 * 60,
        }
        try:
            table.put_item(Item=item, ConditionExpression="attribute_not_exists(event_id)")
            logger.info("Processed mobility event type=%s app_event_id=%s", event_type, app_event_id)
            processed += 1
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                duplicates += 1
                logger.info("Skipping duplicate stream record %s", stream_id)
            else:
                logger.exception("Failed to process stream record %s", stream_id)
                failures.append({"itemIdentifier": stream_id})

    logger.info("Stream batch complete processed=%s duplicates=%s failures=%s", processed, duplicates, len(failures))
    # Partial batch response lets Lambda retry only failed records.
    return {"batchItemFailures": failures}
