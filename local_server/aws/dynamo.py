import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

import boto3
from botocore.exceptions import ClientError

from local_server.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)

_dynamo = None
_apigw = None


def _get_dynamo():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb", region_name=settings.aws_region)
    return _dynamo


def _get_apigw():
    global _apigw
    if _apigw is None:
        _apigw = boto3.client(
            "apigatewaymanagementapi",
            endpoint_url=settings.aws_ws_endpoint,
            region_name=settings.aws_region,
        )
    return _apigw


def _clean(val: Any) -> Any:
    if val is None:
        return None
    if isinstance(val, float):
        return Decimal(str(val))
    if isinstance(val, datetime):
        return val.isoformat()
    if isinstance(val, dict):
        return {k: _clean(v) for k, v in val.items() if v is not None}
    if isinstance(val, list):
        return [_clean(v) for v in val if v is not None]
    return val


def incident_to_dynamo_item(incident) -> dict:
    pub = incident.published_at
    scraped = incident.scraped_at
    pub_str = (pub or scraped or datetime.utcnow()).isoformat() if not isinstance((pub or scraped), str) else (pub or scraped)

    district = (incident.district or "unknown").lower()
    category = incident.category or "Unknown"

    item = {
        "district": district,
        "published_at_id": f"{pub_str}#{incident.id}",
        "cat_time": f"{category}#{pub_str}",
        "category": category,
        "source_id": incident.source_id or "unknown",
        "published_at": pub_str,
        "url": incident.url,
        "id": str(incident.id),
        "title": incident.title,
        "summary": incident.summary,
        "source_name": incident.source_name,
        "viral_score": incident.viral_score or 0,
        "sentiment": incident.sentiment,
        "language": incident.language or "en",
        "confidence": Decimal(str(incident.confidence or 0.0)),
        "entities": _clean(incident.entities) if incident.entities else {},
        "image_url": incident.image_url,
        "scraped_at": scraped.isoformat() if scraped else None,
    }

    return {k: v for k, v in item.items() if v is not None}


def _write_sync(item: dict) -> None:
    table = _get_dynamo().Table(settings.dynamodb_incidents_table)
    table.put_item(Item=item)


def _broadcast_sync(payload: bytes) -> None:
    dynamo = _get_dynamo()
    conn_table = dynamo.Table(settings.dynamodb_ws_connections_table)
    resp = conn_table.scan(ProjectionExpression="connectionId")
    connections = resp.get("Items", [])
    if not connections:
        return

    apigw = _get_apigw()
    stale = []
    for conn in connections:
        cid = conn["connectionId"]
        try:
            apigw.post_to_connection(ConnectionId=cid, Data=payload)
        except ClientError as e:
            if e.response["Error"]["Code"] == "GoneException":
                stale.append(cid)

    for cid in stale:
        conn_table.delete_item(Key={"connectionId": cid})


async def save_incident_to_dynamo(incident) -> None:
    if not settings.dynamodb_incidents_table:
        return
    try:
        item = incident_to_dynamo_item(incident)
        await asyncio.get_event_loop().run_in_executor(None, _write_sync, item)
        logger.info("DynamoDB write OK: %s", incident.url)
    except Exception as exc:
        logger.warning("DynamoDB write failed: %s", exc)


async def broadcast_to_ws(event: dict) -> None:
    if not settings.aws_ws_endpoint:
        return
    try:
        payload = json.dumps(event, default=str).encode()
        await asyncio.get_event_loop().run_in_executor(None, _broadcast_sync, payload)
    except Exception as exc:
        logger.warning("WS broadcast failed: %s", exc)
