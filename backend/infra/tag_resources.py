"""Add Project and Owner tags to every v1.3 demo resource (PRD 10.9).

Run from backend. Without --execute this only prints the plan.
  uv run python infra/tag_resources.py --owner NAME [--execute]
Existing tags are kept; only Project and Owner are set. Dashboards cannot be tagged.
"""

import argparse
import json
from pathlib import Path

import boto3

OUT = Path(__file__).resolve().parents[1] / "deploy" / "aws"
FUNCTION = "securedrop-api"
TABLES = [f"sd_{n}" for n in ("admins", "admin_sessions", "sessions", "transfers", "events")]
ROLES = ["securedrop-lambda-v13", "securedrop-apigateway-logs"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    config = json.loads((OUT / "resources.json").read_text())
    state = json.loads((OUT / "serverless.json").read_text())
    region = state["region"]
    session = boto3.Session(profile_name=config["profile"], region_name=region)
    account = session.client("sts").get_caller_identity()["Account"]
    if account != config["account_id"]:
        raise SystemExit("AWS account mismatch")
    tags = {"Project": "securedrop", "Owner": args.owner}
    pairs = [{"Key": k, "Value": v} for k, v in tags.items()]
    s3, ddb, lam, api, logs, iam, cf, cw = (
        session.client(n)
        for n in (
            "s3",
            "dynamodb",
            "lambda",
            "apigateway",
            "logs",
            "iam",
            "cloudfront",
            "cloudwatch",
        )
    )

    def s3_tags(bucket):
        current = {t["Key"]: t["Value"] for t in s3.get_bucket_tagging(Bucket=bucket)["TagSet"]}
        s3.put_bucket_tagging(
            Bucket=bucket,
            Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in {**current, **tags}.items()]},
        )

    gateway = f"arn:aws:apigateway:{region}::"
    plan = [
        *[
            (f"S3 {b}", lambda b=b: s3_tags(b))
            for b in (state["files_bucket"], state["web_bucket"])
        ],
        *[
            (
                f"DynamoDB {t}",
                lambda t=t: ddb.tag_resource(
                    ResourceArn=f"arn:aws:dynamodb:{region}:{account}:table/{t}", Tags=pairs
                ),
            )
            for t in TABLES
        ],
        (
            f"Lambda {FUNCTION}",
            lambda: lam.tag_resource(
                Resource=f"arn:aws:lambda:{region}:{account}:function:{FUNCTION}", Tags=tags
            ),
        ),
        *[
            (
                f"API Gateway {path}",
                lambda p=path: api.tag_resource(resourceArn=gateway + p, tags=tags),
            )
            for path in (
                f"/restapis/{state['api_id']}",
                f"/restapis/{state['api_id']}/stages/prod",
                f"/usageplans/{state['usage_plan_id']}",
                f"/apikeys/{state['plugin_key_id']}",
            )
        ],
        *[
            (
                f"log group {g}",
                lambda g=g: logs.tag_resource(
                    resourceArn=f"arn:aws:logs:{region}:{account}:log-group:{g}", tags=tags
                ),
            )
            for g in (f"/aws/lambda/{FUNCTION}", "/apigw/securedrop-api")
        ],
        *[(f"IAM role {r}", lambda r=r: iam.tag_role(RoleName=r, Tags=pairs)) for r in ROLES],
        *[
            (
                f"alarm {a}",
                lambda a=a: cw.tag_resource(
                    ResourceARN=f"arn:aws:cloudwatch:{region}:{account}:alarm:{a}", Tags=pairs
                ),
            )
            for a in ("SecureDrop-LambdaErrors", "SecureDrop-Api5XX")
        ],
        (
            "CloudFront " + state["distribution_id"],
            lambda: cf.tag_resource(
                Resource=f"arn:aws:cloudfront::{account}:distribution/{state['distribution_id']}",
                Tags={"Items": pairs},
            ),
        ),
    ]
    # Security resources exist only after configure_security.py has run.
    kms_pairs = [{"TagKey": k, "TagValue": v} for k, v in tags.items()]
    if "kms_key_id" in state:
        kms = session.client("kms")
        plan.append(
            (
                f"KMS key {state['kms_key_id']}",
                lambda: kms.tag_resource(KeyId=state["kms_key_id"], Tags=kms_pairs),
            )
        )
    if "waf_acl_arn" in state:
        waf = session.client("wafv2")
        plan.append(
            (
                f"WAF {state['waf_acl_arn']}",
                lambda: waf.tag_resource(ResourceARN=state["waf_acl_arn"], Tags=pairs),
            )
        )
    if "cloudtrail_arn" in state:
        ct = session.client("cloudtrail")
        plan.append(
            (
                f"CloudTrail {state['cloudtrail_arn']}",
                lambda: ct.add_tags(ResourceId=state["cloudtrail_arn"], TagsList=pairs),
            )
        )
        plan.append(
            (f"S3 {state['cloudtrail_bucket']}", lambda: s3_tags(state["cloudtrail_bucket"]))
        )
    for label, action in plan:
        print(("TAG  " if args.execute else "PLAN ") + label)
        if args.execute:
            action()
    print(f"{tags} {'applied' if args.execute else '(dry run; add --execute)'}")


if __name__ == "__main__":
    main()
