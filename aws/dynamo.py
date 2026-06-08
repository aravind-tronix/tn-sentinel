import os
from decimal import Decimal
from typing import Any

import boto3

_dynamo = None


def _get_dynamo():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION_NAME", "ap-south-1"))
    return _dynamo


def incidents_table():
    return _get_dynamo().Table(os.environ["INCIDENTS_TABLE"])


def connections_table():
    return _get_dynamo().Table(os.environ["WS_CONNECTIONS_TABLE"])


def _to_python(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        n = float(obj)
        return int(n) if n == int(n) else n
    if isinstance(obj, dict):
        return {k: _to_python(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_python(v) for v in obj]
    return obj


def item_to_response(item: dict) -> dict:
    return _to_python(item)
