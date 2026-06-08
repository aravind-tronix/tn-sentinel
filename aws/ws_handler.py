import logging
import os
import time

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

_dynamo = None


def _conn_table():
    global _dynamo
    if _dynamo is None:
        _dynamo = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION_NAME"])
    return _dynamo.Table(os.environ["WS_CONNECTIONS_TABLE"])


TTL_SECONDS = 86400  # 24 h


def handler(event, context):
    route = event["requestContext"]["routeKey"]
    conn_id = event["requestContext"]["connectionId"]
    logger.info("WS route=%s conn=%s", route, conn_id)

    if route == "$connect":
        _conn_table().put_item(Item={
            "connectionId": conn_id,
            "ttl": int(time.time()) + TTL_SECONDS,
        })
        return {"statusCode": 200}

    if route == "$disconnect":
        _conn_table().delete_item(Key={"connectionId": conn_id})
        return {"statusCode": 200}

    # $default — ignore unexpected client messages
    return {"statusCode": 200}
