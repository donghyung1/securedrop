"""Create the five PRD tables using the developer's AWS CLI profile."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import Settings
from store import Store


def create_tables(store):
    client = store.resource.meta.client
    keys = {
        "admins": [("username", "HASH")],
        "admin_sessions": [("token_hash", "HASH")],
        "sessions": [("session_id", "HASH")],
        "transfers": [("transfer_id", "HASH")],
        "events": [("day", "HASH"), ("ts#uuid", "RANGE")],
    }
    for kind, schema in keys.items():
        table_name = getattr(store.settings, f"table_{kind}")
        definitions = [{"AttributeName": name, "AttributeType": "S"} for name, _ in schema]
        options = {}
        if kind == "transfers":
            definitions.extend(
                [
                    {"AttributeName": name, "AttributeType": "S"}
                    for name in ("created_day", "created_at")
                ]
            )
            options["GlobalSecondaryIndexes"] = [
                {
                    "IndexName": "day-created-index",
                    "KeySchema": [
                        {"AttributeName": "created_day", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }
            ]
        try:
            client.create_table(
                TableName=table_name,
                BillingMode="PAY_PER_REQUEST",
                AttributeDefinitions=definitions,
                KeySchema=[{"AttributeName": name, "KeyType": key} for name, key in schema],
                Tags=[{"Key": "Project", "Value": "securedrop"}],
                **options,
            )
        except client.exceptions.ResourceInUseException:
            pass
        client.get_waiter("table_exists").wait(TableName=table_name)
        if kind != "admins":
            ttl = client.describe_time_to_live(TableName=table_name)["TimeToLiveDescription"]
            if ttl["TimeToLiveStatus"] == "DISABLED":
                client.update_time_to_live(
                    TableName=table_name,
                    TimeToLiveSpecification={"Enabled": True, "AttributeName": "ttl"},
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile")
    args = parser.parse_args()
    create_tables(Store(Settings(aws_profile=args.profile)))
    print("DynamoDB tables ready.")
