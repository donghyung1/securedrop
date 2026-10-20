"""Idempotently configure KMS (transfer bucket SSE-KMS), WAFv2 on the API stage and CloudTrail.

Run from backend after deploy_serverless.py:
  uv run python infra/configure_security.py --profile lab [--only kms waf cloudtrail] [--deploy-code]
Resource IDs are recorded in deploy/aws/serverless.json. Without --profile the default
credential chain is used (resources.json names the profile the deploy used).
"""

import argparse
import json
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

OUT = Path(__file__).resolve().parents[1] / "deploy" / "aws"
STATE_PATH = OUT / "serverless.json"
FUNCTION = "securedrop-api"
LAMBDA_ROLE = "securedrop-lambda-v13"
KMS_ALIAS = "alias/securedrop-transfers"
ACL_NAME = "securedrop-api-acl"
TRAIL_NAME = "securedrop-trail"
TAGS = {"Project": "securedrop", "ManagedBy": "securedrop-security"}
# Per-IP requests per 5 minutes. A venue sharing one NAT IP (~100 people x ~15 API calls
# per transfer flow) stays well below this, while one abusive IP is capped at ~10 rps,
# half of the stage-wide 20 rps throttle.
RATE_LIMIT = 3000
# CommonRuleSet body rules that inspect user-supplied file content. The app intentionally
# receives files/JSON with sensitive-looking text (and 4 MiB multipart), so these only Count.
COMMON_COUNT_RULES = [
    "SizeRestrictions_BODY",
    "CrossSiteScripting_BODY",
    "GenericLFI_BODY",
    "GenericRFI_BODY",
    "EC2MetaDataSSRF_BODY",
]
TRAIL_RETENTION_DAYS = 90


def key_policy(account, role_arn, bucket, region):
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "EnableAccountAdmin",
                "Effect": "Allow",
                "Principal": {"AWS": f"arn:aws:iam::{account}:root"},
                "Action": "kms:*",
                "Resource": "*",
            },
            {
                "Sid": "LambdaUseKeyViaS3Only",
                "Effect": "Allow",
                "Principal": {"AWS": role_arn},
                "Action": ["kms:GenerateDataKey", "kms:Decrypt"],
                "Resource": "*",
                "Condition": {
                    "StringEquals": {"kms:ViaService": f"s3.{region}.amazonaws.com"},
                    "StringLike": {
                        "kms:EncryptionContext:aws:s3:arn": [
                            f"arn:aws:s3:::{bucket}",
                            f"arn:aws:s3:::{bucket}/*",
                        ]
                    },
                },
            },
        ],
    }


def waf_rules(rate_limit=RATE_LIMIT):
    def visibility(name):
        return {
            "SampledRequestsEnabled": True,
            "CloudWatchMetricsEnabled": True,
            "MetricName": name,
        }

    def managed(name, priority, overrides=()):
        statement = {"VendorName": "AWS", "Name": name}
        if overrides:
            statement["RuleActionOverrides"] = [
                {"Name": rule, "ActionToUse": {"Count": {}}} for rule in overrides
            ]
        return {
            "Name": name,
            "Priority": priority,
            "Statement": {"ManagedRuleGroupStatement": statement},
            "OverrideAction": {"None": {}},
            "VisibilityConfig": visibility(name),
        }

    return [
        {
            "Name": "RateLimitPerIp",
            "Priority": 0,
            "Statement": {
                "RateBasedStatement": {
                    "Limit": rate_limit,
                    "AggregateKeyType": "IP",
                    "EvaluationWindowSec": 300,
                }
            },
            "Action": {"Block": {}},
            "VisibilityConfig": visibility("RateLimitPerIp"),
        },
        managed("AWSManagedRulesAmazonIpReputationList", 1),
        managed("AWSManagedRulesKnownBadInputsRuleSet", 2),
        managed("AWSManagedRulesCommonRuleSet", 3, COMMON_COUNT_RULES),
    ]


def trail_bucket_policy(bucket, account, trail_arn):
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AWSCloudTrailAclCheck",
                "Effect": "Allow",
                "Principal": {"Service": "cloudtrail.amazonaws.com"},
                "Action": "s3:GetBucketAcl",
                "Resource": f"arn:aws:s3:::{bucket}",
                "Condition": {"StringEquals": {"aws:SourceArn": trail_arn}},
            },
            {
                "Sid": "AWSCloudTrailWrite",
                "Effect": "Allow",
                "Principal": {"Service": "cloudtrail.amazonaws.com"},
                "Action": "s3:PutObject",
                "Resource": f"arn:aws:s3:::{bucket}/AWSLogs/{account}/*",
                "Condition": {"StringEquals": {"aws:SourceArn": trail_arn}},
            },
            {
                "Sid": "DenyInsecureTransport",
                "Effect": "Deny",
                "Principal": "*",
                "Action": "s3:*",
                "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
                "Condition": {"Bool": {"aws:SecureTransport": "false"}},
            },
        ],
    }


def event_selectors(files_bucket):
    return [
        {
            "Name": "Management events",
            "FieldSelectors": [{"Field": "eventCategory", "Equals": ["Management"]}],
        },
        {
            "Name": "Transfer object data events",
            "FieldSelectors": [
                {"Field": "eventCategory", "Equals": ["Data"]},
                {"Field": "resources.type", "Equals": ["AWS::S3::Object"]},
                {
                    "Field": "resources.ARN",
                    "StartsWith": [f"arn:aws:s3:::{files_bucket}/transfers/"],
                },
            ],
        },
    ]


def pairs(tags=TAGS):
    return [{"Key": k, "Value": v} for k, v in tags.items()]


class Security:
    def __init__(self, session, region, state):
        self.s, self.region, self.state = session, region, state
        self.account = session.client("sts").get_caller_identity()["Account"]

    def save(self, **values):
        self.state.update(values)
        STATE_PATH.write_text(json.dumps(self.state, indent=2) + "\n", encoding="utf-8")

    def kms(self):
        kms, iam = self.s.client("kms"), self.s.client("iam")
        bucket = self.state["files_bucket"]
        role_arn = iam.get_role(RoleName=LAMBDA_ROLE)["Role"]["Arn"]
        policy = json.dumps(key_policy(self.account, role_arn, bucket, self.region))
        try:
            key = kms.describe_key(KeyId=KMS_ALIAS)["KeyMetadata"]
            kms.put_key_policy(KeyId=key["KeyId"], PolicyName="default", Policy=policy)
        except kms.exceptions.NotFoundException:
            key = kms.create_key(
                Description="SecureDrop transfer bucket (SSE-KMS)",
                KeyUsage="ENCRYPT_DECRYPT",
                KeySpec="SYMMETRIC_DEFAULT",
                Policy=policy,
                Tags=[{"TagKey": k, "TagValue": v} for k, v in TAGS.items()],
            )["KeyMetadata"]
            kms.create_alias(AliasName=KMS_ALIAS, TargetKeyId=key["KeyId"])
        kms.enable_key_rotation(KeyId=key["KeyId"])
        kms.tag_resource(
            KeyId=key["KeyId"], Tags=[{"TagKey": k, "TagValue": v} for k, v in TAGS.items()]
        )
        self.save(kms_key_id=key["KeyId"], kms_key_arn=key["Arn"], kms_alias=KMS_ALIAS)
        print(f"KMS key {key['KeyId']} ({KMS_ALIAS}) rotation on")
        return key["Arn"]

    def apply_kms(self, key_arn, deploy_code):
        """Lambda first (it supports both modes), then switch the bucket default."""
        lam, s3 = self.s.client("lambda"), self.s.client("s3")
        if deploy_code:
            package = (
                Path(__file__).resolve().parents[1] / "dist" / "securedrop-api.zip"
            ).read_bytes()
            lam.update_function_code(FunctionName=FUNCTION, ZipFile=package)
            lam.get_waiter("function_updated_v2").wait(FunctionName=FUNCTION)
            print("Lambda code updated")
        config = lam.get_function_configuration(FunctionName=FUNCTION)
        variables = {**config["Environment"]["Variables"], "KMS_KEY_ID": key_arn}
        for attempt in range(10):  # key policy propagation
            try:
                lam.update_function_configuration(
                    FunctionName=FUNCTION, Environment={"Variables": variables}
                )
                break
            except lam.exceptions.ResourceConflictException:
                time.sleep(3)
        lam.get_waiter("function_updated_v2").wait(FunctionName=FUNCTION)
        s3.put_bucket_encryption(
            Bucket=self.state["files_bucket"],
            ServerSideEncryptionConfiguration={
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "aws:kms",
                            "KMSMasterKeyID": key_arn,
                        },
                        "BucketKeyEnabled": True,
                    }
                ]
            },
        )
        print("Lambda env KMS_KEY_ID set; bucket default encryption = SSE-KMS + bucket key")

    def waf(self):
        waf = self.s.client("wafv2")
        args = {
            "Name": ACL_NAME,
            "Scope": "REGIONAL",
            "DefaultAction": {"Allow": {}},
            "Description": "SecureDrop API: managed rules + per-IP rate limit",
            "Rules": waf_rules(),
            "VisibilityConfig": {
                "SampledRequestsEnabled": True,
                "CloudWatchMetricsEnabled": True,
                "MetricName": ACL_NAME,
            },
        }
        found = [a for a in waf.list_web_acls(Scope="REGIONAL")["WebACLs"] if a["Name"] == ACL_NAME]
        if found:
            lock = waf.get_web_acl(Name=ACL_NAME, Scope="REGIONAL", Id=found[0]["Id"])["LockToken"]
            waf.update_web_acl(Id=found[0]["Id"], LockToken=lock, **args)
            summary = found[0]
        else:
            summary = waf.create_web_acl(Tags=pairs(), **args)["Summary"]
        stage = f"arn:aws:apigateway:{self.region}::/restapis/{self.state['api_id']}/stages/prod"
        for attempt in range(10):  # a new ACL can take a few seconds to become associable
            try:
                waf.associate_web_acl(WebACLArn=summary["ARN"], ResourceArn=stage)
                break
            except waf.exceptions.WAFUnavailableEntityException:
                if attempt == 9:
                    raise
                time.sleep(5)
        self.save(waf_acl_id=summary["Id"], waf_acl_arn=summary["ARN"], waf_acl_name=ACL_NAME)
        print(f"WAF {ACL_NAME} associated with {stage}")

    def cloudtrail(self):
        s3, ct = self.s.client("s3"), self.s.client("cloudtrail")
        bucket = f"securedrop-cloudtrail-{self.account}-{self.region}"
        trail_arn = f"arn:aws:cloudtrail:{self.region}:{self.account}:trail/{TRAIL_NAME}"
        try:
            s3.head_bucket(Bucket=bucket)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
                raise
            s3.create_bucket(
                Bucket=bucket, CreateBucketConfiguration={"LocationConstraint": self.region}
            )
        s3.put_bucket_tagging(Bucket=bucket, Tagging={"TagSet": pairs()})
        s3.put_public_access_block(
            Bucket=bucket,
            PublicAccessBlockConfiguration={
                k: True
                for k in (
                    "BlockPublicAcls",
                    "IgnorePublicAcls",
                    "BlockPublicPolicy",
                    "RestrictPublicBuckets",
                )
            },
        )
        s3.put_bucket_ownership_controls(
            Bucket=bucket, OwnershipControls={"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]}
        )
        s3.put_bucket_encryption(
            Bucket=bucket,
            ServerSideEncryptionConfiguration={
                "Rules": [{"ApplyServerSideEncryptionByDefault": {"SSEAlgorithm": "AES256"}}]
            },
        )
        s3.put_bucket_lifecycle_configuration(
            Bucket=bucket,
            LifecycleConfiguration={
                "Rules": [
                    {
                        "ID": "expire-logs",
                        "Status": "Enabled",
                        "Filter": {"Prefix": ""},
                        "Expiration": {"Days": TRAIL_RETENTION_DAYS},
                        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 1},
                    }
                ]
            },
        )
        s3.put_bucket_policy(
            Bucket=bucket,
            Policy=json.dumps(trail_bucket_policy(bucket, self.account, trail_arn)),
        )
        options = {
            "Name": TRAIL_NAME,
            "S3BucketName": bucket,
            "IsMultiRegionTrail": True,
            "EnableLogFileValidation": True,
            "IncludeGlobalServiceEvents": True,
        }
        existing = ct.describe_trails(trailNameList=[TRAIL_NAME], includeShadowTrails=False)
        if existing["trailList"]:
            ct.update_trail(**options)
        else:
            ct.create_trail(TagsList=pairs(), **options)
        ct.put_event_selectors(
            TrailName=TRAIL_NAME,
            AdvancedEventSelectors=event_selectors(self.state["files_bucket"]),
        )
        ct.start_logging(Name=TRAIL_NAME)
        self.save(cloudtrail_bucket=bucket, cloudtrail_name=TRAIL_NAME, cloudtrail_arn=trail_arn)
        print(f"CloudTrail {TRAIL_NAME} logging to {bucket}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile")
    parser.add_argument("--region", default="ap-northeast-2")
    parser.add_argument("--only", nargs="+", choices=["kms", "waf", "cloudtrail"])
    parser.add_argument(
        "--deploy-code", action="store_true", help="also upload dist/securedrop-api.zip to Lambda"
    )
    args = parser.parse_args()
    state = json.loads(STATE_PATH.read_text())
    if state["region"] != args.region:
        sys.exit("region differs from serverless.json")
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    run = Security(session, args.region, state)
    resources = OUT / "resources.json"
    if resources.exists() and run.account != json.loads(resources.read_text())["account_id"]:
        sys.exit("AWS account mismatch")
    wanted = args.only or ["kms", "waf", "cloudtrail"]
    if "kms" in wanted:
        run.apply_kms(run.kms(), args.deploy_code)
    if "waf" in wanted:
        run.waf()
    if "cloudtrail" in wanted:
        run.cloudtrail()
    print("Security configuration complete.")


if __name__ == "__main__":
    main()
