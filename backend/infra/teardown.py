"""Tear down the v1.3 serverless demo after the presentation.

Run from backend. Without --execute this only prints the plan and tag checks.
  uv run python infra/teardown.py                       # dry run
  uv run python infra/teardown.py --api-key-only --execute --confirm ACCOUNT_ID
  uv run python infra/teardown.py --execute --confirm ACCOUNT_ID [--include-cloudfront]

Also removes the WAF web ACL, CloudTrail trail + log bucket and (7-day scheduled) KMS key.
Every resource must carry Project=securedrop or match an ID saved in
deploy/aws/serverless.json. Anything else is skipped and reported.
"""

import argparse
import json
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "deploy" / "aws"
FUNCTION = "securedrop-api"
TABLES = [f"sd_{n}" for n in ("admins", "admin_sessions", "sessions", "transfers", "events")]
LOG_GROUPS = [f"/aws/lambda/{FUNCTION}", "/apigw/securedrop-api"]
ALARMS = ["SecureDrop-LambdaErrors", "SecureDrop-Api5XX"]
DASHBOARD = "SecureDrop-Demo"
ROLES = ["securedrop-lambda-v13", "securedrop-apigateway-logs"]


def tagged(tags):
    if isinstance(tags, list):
        tags = {t["Key"]: t["Value"] for t in tags}
    return (tags or {}).get("Project") == "securedrop"


class Teardown:
    def __init__(self, session, state, execute):
        self.s, self.state, self.execute = session, state, execute
        self.skipped = []

    def step(self, label, ok, action):
        if not ok:
            self.skipped.append(label)
            print(f"SKIP   {label} (tag/ID check failed)")
            return
        print(f"{'DELETE' if self.execute else 'PLAN  '} {label}")
        if self.execute:
            try:
                action()
            except ClientError as exc:
                self.skipped.append(label)
                print(
                    f"  ERROR {exc.response['Error']['Code']}: {exc.response['Error']['Message']}"
                )

    def api_keys(self):
        api = self.s.client("apigateway")
        for key in api.get_api_keys(includeValues=False)["items"]:
            if key["name"].startswith("securedrop") or key["id"] == self.state.get("plugin_key_id"):
                self.step(
                    f"API key {key['name']} ({key['id']})",
                    tagged(key.get("tags")) or key["id"] == self.state.get("plugin_key_id"),
                    lambda k=key: api.delete_api_key(apiKey=k["id"]),
                )

    def gateway(self):
        api = self.s.client("apigateway")
        for plan in api.get_usage_plans()["items"]:
            if plan["name"].startswith("securedrop"):

                def drop(p=plan):
                    stages = [f"{s['apiId']}:{s['stage']}" for s in p.get("apiStages", [])]
                    if stages:
                        api.update_usage_plan(
                            usagePlanId=p["id"],
                            patchOperations=[
                                {"op": "remove", "path": "/apiStages", "value": s} for s in stages
                            ],
                        )
                    api.delete_usage_plan(usagePlanId=p["id"])

                self.step(
                    f"usage plan {plan['name']} ({plan['id']})", tagged(plan.get("tags")), drop
                )
        for rest in api.get_rest_apis()["items"]:
            if rest["name"] == FUNCTION:
                self.step(
                    f"REST API {rest['id']}",
                    tagged(rest.get("tags")) or rest["id"] == self.state.get("api_id"),
                    lambda r=rest: api.delete_rest_api(restApiId=r["id"]),
                )

    def function(self):
        lam = self.s.client("lambda")
        try:
            tags = lam.get_function(FunctionName=FUNCTION).get("Tags")
        except lam.exceptions.ResourceNotFoundException:
            return
        self.step(
            f"Lambda {FUNCTION}", tagged(tags), lambda: lam.delete_function(FunctionName=FUNCTION)
        )

    def cloudfront(self):
        cf = self.s.client("cloudfront")
        distribution = self.state.get("distribution_id")
        web = self.state.get("web_bucket", "")
        if distribution:
            try:
                current = cf.get_distribution_config(Id=distribution)
            except cf.exceptions.NoSuchDistribution:
                current = None
            if current:
                origins = [
                    o["DomainName"] for o in current["DistributionConfig"]["Origins"]["Items"]
                ]

                def drop():
                    config = current["DistributionConfig"]
                    etag = current["ETag"]
                    if config["Enabled"]:
                        config["Enabled"] = False
                        etag = cf.update_distribution(
                            Id=distribution, IfMatch=etag, DistributionConfig=config
                        )["ETag"]
                        print("  waiting for disabled distribution to deploy (up to ~15 min)")
                    cf.get_waiter("distribution_deployed").wait(
                        Id=distribution, WaiterConfig={"Delay": 30, "MaxAttempts": 40}
                    )
                    etag = cf.get_distribution_config(Id=distribution)["ETag"]
                    cf.delete_distribution(Id=distribution, IfMatch=etag)

                self.step(
                    f"CloudFront {distribution} {current['DistributionConfig'].get('Aliases', {}).get('Items', [])}",
                    any(o.startswith(web + ".") for o in origins),
                    drop,
                )
        for key, getter, deleter, label in (
            (
                "response_headers_policy_id",
                cf.get_response_headers_policy,
                cf.delete_response_headers_policy,
                "response headers policy",
            ),
            ("oac_id", cf.get_origin_access_control, cf.delete_origin_access_control, "OAC"),
        ):
            if key in self.state:
                ident = self.state[key]
                self.step(
                    f"CloudFront {label} {ident}",
                    True,
                    lambda i=ident, g=getter, d=deleter: d(Id=i, IfMatch=g(Id=i)["ETag"]),
                )

    def buckets(self):
        s3 = self.s.resource("s3")
        client = self.s.client("s3")
        for name in (self.state.get("files_bucket"), self.state.get("web_bucket")):
            if not name:
                continue
            try:
                tags = client.get_bucket_tagging(Bucket=name)["TagSet"]
            except ClientError as exc:
                if exc.response["Error"]["Code"] == "NoSuchBucket":
                    continue
                tags = []

            def drop(n=name):
                bucket = s3.Bucket(n)
                bucket.object_versions.delete()
                bucket.objects.delete()
                bucket.delete()

            self.step(f"S3 bucket {name} (all objects)", tagged(tags), drop)

    def tables(self):
        ddb = self.s.client("dynamodb")
        for name in TABLES:
            try:
                arn = ddb.describe_table(TableName=name)["Table"]["TableArn"]
            except ddb.exceptions.ResourceNotFoundException:
                continue
            tags = ddb.list_tags_of_resource(ResourceArn=arn).get("Tags")
            self.step(
                f"DynamoDB {name}", tagged(tags), lambda n=name: ddb.delete_table(TableName=n)
            )

    def monitoring(self):
        logs, cw = self.s.client("logs"), self.s.client("cloudwatch")
        for group in LOG_GROUPS:
            found = [
                g
                for g in logs.describe_log_groups(logGroupNamePrefix=group)["logGroups"]
                if g["logGroupName"] == group
            ]
            if found:
                tags = logs.list_tags_for_resource(resourceArn=found[0]["arn"].rstrip(":*")).get(
                    "tags"
                )
                self.step(
                    f"log group {group}",
                    tagged(tags),
                    lambda g=group: logs.delete_log_group(logGroupName=g),
                )
        existing = [a["AlarmName"] for a in cw.describe_alarms(AlarmNames=ALARMS)["MetricAlarms"]]
        if existing:
            self.step(f"alarms {existing}", True, lambda: cw.delete_alarms(AlarmNames=existing))
        if cw.list_dashboards(DashboardNamePrefix=DASHBOARD)["DashboardEntries"]:
            self.step(
                f"dashboard {DASHBOARD}",
                True,
                lambda: cw.delete_dashboards(DashboardNames=[DASHBOARD]),
            )

    def waf(self):
        waf = self.s.client("wafv2")
        name = self.state.get("waf_acl_name", "securedrop-api-acl")
        found = [a for a in waf.list_web_acls(Scope="REGIONAL")["WebACLs"] if a["Name"] == name]
        if not found:
            return
        acl = found[0]
        tags = waf.list_tags_for_resource(ResourceARN=acl["ARN"])["TagInfoForResource"]
        ok = tagged(tags.get("TagList")) or acl["Id"] == self.state.get("waf_acl_id")

        def drop():
            for arn in waf.list_resources_for_web_acl(
                WebACLArn=acl["ARN"], ResourceType="API_GATEWAY"
            )["ResourceArns"]:
                waf.disassociate_web_acl(ResourceArn=arn)
            lock = waf.get_web_acl(Name=name, Scope="REGIONAL", Id=acl["Id"])["LockToken"]
            waf.delete_web_acl(Name=name, Scope="REGIONAL", Id=acl["Id"], LockToken=lock)

        self.step(f"WAF web ACL {name} (disassociate first)", ok, drop)

    def cloudtrail(self):
        ct, s3 = self.s.client("cloudtrail"), self.s.resource("s3")
        client = self.s.client("s3")
        name = self.state.get("cloudtrail_name", "securedrop-trail")
        trails = ct.describe_trails(trailNameList=[name], includeShadowTrails=False)["trailList"]
        if trails:
            arn = trails[0]["TrailARN"]
            tags = ct.list_tags(ResourceIdList=[arn])["ResourceTagList"][0]["TagsList"]

            def stop_and_delete():
                ct.stop_logging(Name=name)
                ct.delete_trail(Name=name)

            self.step(
                f"CloudTrail {name} (stop logging, delete)",
                tagged(tags) or arn == self.state.get("cloudtrail_arn"),
                stop_and_delete,
            )
        bucket = self.state.get("cloudtrail_bucket")
        if bucket:
            try:
                tags = client.get_bucket_tagging(Bucket=bucket)["TagSet"]
            except ClientError as exc:
                if exc.response["Error"]["Code"] == "NoSuchBucket":
                    return
                tags = []

            def drop():
                b = s3.Bucket(bucket)
                b.object_versions.delete()
                b.objects.delete()
                b.delete()

            self.step(f"S3 bucket {bucket} (CloudTrail logs, all objects)", tagged(tags), drop)

    def kms(self):
        kms = self.s.client("kms")
        alias = self.state.get("kms_alias", "alias/securedrop-transfers")
        try:
            key = kms.describe_key(KeyId=alias)["KeyMetadata"]
        except kms.exceptions.NotFoundException:
            return
        tags = [
            {"Key": t["TagKey"], "Value": t["TagValue"]}
            for t in kms.list_resource_tags(KeyId=key["KeyId"])["Tags"]
        ]

        def drop():
            kms.delete_alias(AliasName=alias)
            if key["KeyState"] != "PendingDeletion":
                kms.schedule_key_deletion(KeyId=key["KeyId"], PendingWindowInDays=7)

        self.step(
            f"KMS key {key['KeyId']} ({alias}; delete alias, schedule deletion in 7 days)",
            tagged(tags) or key["KeyId"] == self.state.get("kms_key_id"),
            drop,
        )

    def roles(self):
        iam = self.s.client("iam")
        gateway_role = self.s.client("apigateway").get_account().get("cloudwatchRoleArn", "")
        for name in ROLES:
            try:
                role = iam.get_role(RoleName=name)["Role"]
            except iam.exceptions.NoSuchEntityException:
                continue
            if role["Arn"] == gateway_role:
                self.skipped.append(f"IAM role {name}")
                print(
                    f"SKIP   IAM role {name} (account-wide API Gateway logging role; remove manually if unused)"
                )
                continue

            def drop(n=name):
                for policy in iam.list_role_policies(RoleName=n)["PolicyNames"]:
                    iam.delete_role_policy(RoleName=n, PolicyName=policy)
                for policy in iam.list_attached_role_policies(RoleName=n)["AttachedPolicies"]:
                    iam.detach_role_policy(RoleName=n, PolicyArn=policy["PolicyArn"])
                iam.delete_role(RoleName=n)

            self.step(f"IAM role {name}", tagged(role.get("Tags")), drop)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="actually delete")
    parser.add_argument("--confirm", help="account ID, required with --execute")
    parser.add_argument(
        "--api-key-only", action="store_true", help="only delete the plugin API key"
    )
    parser.add_argument(
        "--include-cloudfront", action="store_true", help="also disable and delete the distribution"
    )
    args = parser.parse_args()
    config = json.loads((OUT / "resources.json").read_text())
    state = json.loads((OUT / "serverless.json").read_text())
    session = boto3.Session(profile_name=config["profile"], region_name=state["region"])
    account = session.client("sts").get_caller_identity()["Account"]
    if account != config["account_id"]:
        raise SystemExit("AWS account mismatch")
    if args.execute and args.confirm != account:
        raise SystemExit("--execute requires --confirm <account ID>")
    print(
        f"account {account}, region {state['region']}, {'EXECUTE' if args.execute else 'DRY RUN'}"
    )
    run = Teardown(session, state, args.execute)
    run.api_keys()
    if not args.api_key_only:
        run.waf()
        run.gateway()
        run.function()
        if args.include_cloudfront:
            run.cloudfront()
        else:
            print(
                "KEEP   CloudFront distribution, OAC and headers policy (use --include-cloudfront)"
            )
        run.buckets()
        run.cloudtrail()
        run.kms()
        run.tables()
        run.monitoring()
        run.roles()
    if run.skipped:
        print("Skipped:", *run.skipped, sep="\n  ")
    print(
        "Manual: Route 53 record, ACM certificate, legacy EC2 role securedrop-cloud-backend, "
        f"legacy bucket securedrop-cloud-{account}-{state['region']} (Project=SecureDropCloud)."
    )


if __name__ == "__main__":
    main()
