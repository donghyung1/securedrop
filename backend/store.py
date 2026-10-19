"""DynamoDB persistence. TTL is cleanup only; callers enforce expiry on reads."""

from datetime import datetime, timedelta, timezone
from functools import cached_property, wraps
from uuid import uuid4

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError

from errors import APIError


def now():
    return datetime.now(timezone.utc)


def aws_errors(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise APIError(
                    409, "STATE_CONFLICT", "상태가 변경되었습니다. 다시 확인하세요."
                ) from exc
            raise APIError(502, "STORAGE_ERROR", "저장소 요청에 실패했습니다.") from exc
        except BotoCoreError as exc:
            raise APIError(502, "STORAGE_ERROR", "저장소 요청에 실패했습니다.") from exc

    return wrapped


class Store:
    def __init__(self, settings):
        self.settings = settings

    @cached_property
    def resource(self):
        return boto3.Session(
            profile_name=self.settings.aws_profile, region_name=self.settings.aws_region
        ).resource("dynamodb")

    def table(self, kind):
        return self.resource.Table(getattr(self.settings, f"table_{kind}"))

    @aws_errors
    def get(self, kind, key):
        return self.table(kind).get_item(Key=key, ConsistentRead=True).get("Item")

    @aws_errors
    def put(self, kind, item):
        self.table(kind).put_item(Item=item)

    @aws_errors
    def delete(self, kind, key):
        self.table(kind).delete_item(Key=key)

    def transfer(self, identifier):
        return self.get("transfers", {"transfer_id": identifier})

    @aws_errors
    def transition(self, identifier, expected, target, **fields):
        names = {"#status": "status"}
        values = {":expected": expected, ":target": target}
        updates = ["#status = :target"]
        for i, (key, value) in enumerate(fields.items()):
            names[f"#f{i}"] = key
            values[f":v{i}"] = value
            updates.append(f"#f{i} = :v{i}")
        condition = "#status = :expected"
        if target == "ACTIVE":
            condition += " AND expires_at > :now"
            values[":now"] = now().isoformat()
        return self.table("transfers").update_item(
            Key={"transfer_id": identifier},
            UpdateExpression="SET " + ", ".join(updates),
            ConditionExpression=condition,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ReturnValues="ALL_NEW",
        )["Attributes"]

    @aws_errors
    def count_download(self, identifier):
        self.table("transfers").update_item(
            Key={"transfer_id": identifier},
            UpdateExpression="ADD download_count :one",
            ConditionExpression="#s = :active AND expires_at > :now",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":one": 1, ":active": "ACTIVE", ":now": now().isoformat()},
        )

    def log(self, event, types, source, request_id=""):
        timestamp = now()
        self.put(
            "events",
            {
                "day": timestamp.date().isoformat(),
                "ts#uuid": timestamp.isoformat() + "#" + uuid4().hex,
                "time": timestamp.isoformat(),
                "event": event,
                "types": sorted(set(types)),
                "source": source,
                "request_id": request_id,
                "ttl": int((timestamp + timedelta(days=30)).timestamp()),
            },
        )

    @aws_errors
    def query_day(self, kind, day, limit=None):
        kwargs = {
            "KeyConditionExpression": Key("day" if kind == "events" else "created_day").eq(day),
            "ScanIndexForward": False,
        }
        if kind == "transfers":
            kwargs["IndexName"] = "day-created-index"
        if limit:
            kwargs["Limit"] = limit
        rows = []
        while True:
            result = self.table(kind).query(**kwargs)
            rows.extend(result["Items"])
            if "LastEvaluatedKey" not in result or (limit and len(rows) >= limit):
                return rows[:limit] if limit else rows
            kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
            if limit:
                kwargs["Limit"] = limit - len(rows)

    @aws_errors
    def login_update(self, admin, success):
        failures = int(admin.get("failed_count", 0))
        locked = int(admin.get("locked_until", 0))
        if locked and locked <= int(now().timestamp()):
            failures = 0
        count = 0 if success else failures + 1
        until = (
            0
            if success
            else (
                int(now().timestamp()) + self.settings.admin_login_lock_sec
                if count >= self.settings.admin_login_max_fails
                else 0
            )
        )
        self.table("admins").update_item(
            Key={"username": admin["username"]},
            UpdateExpression="SET failed_count = :count, locked_until = :until",
            ConditionExpression="password_hash = :hash AND failed_count = :old AND locked_until = :lock",
            ExpressionAttributeValues={
                ":count": count,
                ":until": until,
                ":hash": admin["password_hash"],
                ":old": admin.get("failed_count", 0),
                ":lock": admin.get("locked_until", 0),
            },
        )
        return until
