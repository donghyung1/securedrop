"""Configure PRD CloudWatch retention, dashboard and two actionless alarms."""

import argparse
import json

import boto3


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile")
    parser.add_argument("--api-id", required=True)
    parser.add_argument("--function", default="securedrop-api")
    parser.add_argument("--region", default="ap-northeast-2")
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    logs, cw = session.client("logs"), session.client("cloudwatch")
    for group in (f"/aws/lambda/{args.function}", "/apigw/securedrop-api"):
        try:
            logs.create_log_group(logGroupName=group, tags={"Project": "securedrop"})
        except logs.exceptions.ResourceAlreadyExistsException:
            pass
        logs.put_retention_policy(logGroupName=group, retentionInDays=14)
    api_name = session.client("apigateway").get_rest_api(restApiId=args.api_id)["name"]
    alarms = [
        ("SecureDrop-LambdaErrors", "AWS/Lambda", "Errors", "FunctionName", args.function),
        ("SecureDrop-Api5XX", "AWS/ApiGateway", "5XXError", "ApiName", api_name),
    ]
    for name, namespace, metric, dimension, value in alarms:
        cw.put_metric_alarm(
            AlarmName=name,
            Namespace=namespace,
            MetricName=metric,
            Dimensions=[{"Name": dimension, "Value": value}],
            Statistic="Sum",
            Period=60,
            EvaluationPeriods=1,
            Threshold=1,
            ComparisonOperator="GreaterThanOrEqualToThreshold",
            TreatMissingData="notBreaching",
            ActionsEnabled=False,
        )
    widgets = []
    series = [
        (
            "Lambda",
            [
                ["AWS/Lambda", metric, "FunctionName", args.function]
                for metric in ("Invocations", "Errors", "Duration")
            ],
        ),
        (
            "API",
            [
                ["AWS/ApiGateway", metric, "ApiName", api_name]
                for metric in ("Count", "5XXError", "Latency")
            ],
        ),
        (
            "Blocks by type and source",
            [
                [
                    {
                        "expression": "SEARCH('{SecureDrop,Type,Source} MetricName=\"BlockedCount\"', 'Sum', 60)",
                        "id": "blocks",
                    }
                ]
            ],
        ),
        (
            "Transfers",
            [
                [
                    {
                        "expression": "SEARCH('{SecureDrop,Type,Source} MetricName=\"TransferCount\"', 'Sum', 60)",
                        "id": "transfers",
                    }
                ]
            ],
        ),
    ]
    for index, (title, metrics) in enumerate(series):
        widgets.append(
            {
                "type": "metric",
                "x": (index % 2) * 12,
                "y": (index // 2) * 6,
                "width": 12,
                "height": 6,
                "properties": {
                    "region": args.region,
                    "title": title,
                    "metrics": metrics,
                    "period": 60,
                    "stat": "Sum",
                },
            }
        )
    cw.put_dashboard(
        DashboardName="SecureDrop-Demo", DashboardBody=json.dumps({"widgets": widgets})
    )
    print("CloudWatch monitoring configured.")


if __name__ == "__main__":
    main()
