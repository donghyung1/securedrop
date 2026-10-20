"""Verify live KMS / WAF / CloudTrail settings and that a transfer still works end to end.

Run from backend: uv run python infra/verify_security.py [--profile NAME]
Creates two short-lived transfers and revokes them; prints no secret values.
"""

import argparse
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import boto3
import httpx

OUT = Path(__file__).resolve().parents[1] / "deploy" / "aws"
STATE = json.loads((OUT / "serverless.json").read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile")
    args = parser.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=STATE["region"])
    s3, kms, ct, waf, cw = (
        session.client(n) for n in ("s3", "kms", "cloudtrail", "wafv2", "cloudwatch")
    )
    results = {}
    key = kms.describe_key(KeyId=STATE["kms_key_id"])["KeyMetadata"]
    assert key["KeyState"] == "Enabled"
    assert kms.get_key_rotation_status(KeyId=key["KeyId"])["KeyRotationEnabled"]
    rule = s3.get_bucket_encryption(Bucket=STATE["files_bucket"])[
        "ServerSideEncryptionConfiguration"
    ]["Rules"][0]
    assert rule["ApplyServerSideEncryptionByDefault"]["KMSMasterKeyID"] == key["Arn"]
    assert rule["BucketKeyEnabled"] is True
    results["kms_key_rotation_bucket_default"] = "passed"

    status = ct.get_trail_status(Name=STATE["cloudtrail_name"])
    trail = ct.describe_trails(trailNameList=[STATE["cloudtrail_name"]])["trailList"][0]
    assert status["IsLogging"] and trail["IsMultiRegionTrail"] and trail["LogFileValidationEnabled"]
    results["cloudtrail_logging"] = "passed"

    stage = f"arn:aws:apigateway:{STATE['region']}::/restapis/{STATE['api_id']}/stages/prod"
    assoc = waf.get_web_acl_for_resource(ResourceArn=stage)["WebACL"]
    assert assoc["ARN"] == STATE["waf_acl_arn"]
    results["waf_associated"] = "passed"

    with httpx.Client(
        base_url=STATE["api_base"], timeout=60, headers={"Origin": STATE["url"]}
    ) as client:
        # ~4 MiB multipart scan must not be blocked by WAF body rules.
        body = (b"Contact: <script>alert(1)</script> ../../etc/passwd 010-1234-5678\n" * 70000)[
            : 4 * 1024 * 1024 - 1024
        ]
        response = client.post("/api/scan/file", files={"file": ("big.txt", body, "text/plain")})
        assert response.status_code == 200, (response.status_code, response.text[:200])
        assert response.json()["blocked"] is True  # app-level detection, not WAF
        results["scan_4mib_multipart_not_blocked"] = "passed"

        original = b"Contact: 010-1234-5678\n"
        scan = client.post("/api/scan/file", files={"file": ("kms.txt", original, "text/plain")})
        clean = client.post(
            "/api/sanitize",
            files={"file": ("kms.txt", original, "text/plain")},
            data={"actions": json.dumps([{"action": "MASK", "targets": ["PHONE"]}])},
        ).json()
        content = base64.b64decode(clean["content_b64"])
        meta = {
            "name": "kms.txt",
            "size": len(content),
            "type": "text/plain",
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        pending = client.post(
            "/api/transfers",
            json={
                "files": [meta],
                "sanitize_id": clean["sanitize_id"],
                "user_approved": True,
                "expires_in": 300,
            },
        )
        assert pending.status_code == 200, (scan.status_code, pending.text[:200])
        pending = pending.json()
        managed = {"X-Manage-Token": pending["manage_token"]}
        ids = [(pending["id"], managed)]
        try:
            upload = pending["uploads"][0]
            assert upload["fields"]["x-amz-server-side-encryption"] == "aws:kms"
            posted = httpx.post(
                upload["url"],
                data=upload["fields"],
                files={"file": ("kms.txt", content, "text/plain")},
                headers={"Origin": STATE["url"]},
                timeout=60,
            )
            assert posted.status_code == 204, (posted.status_code, posted.text[:300])
            head = s3.head_object(Bucket=STATE["files_bucket"], Key=upload["fields"]["key"])
            assert head["ServerSideEncryption"] == "aws:kms"
            assert head["SSEKMSKeyId"] == key["Arn"] and head["BucketKeyEnabled"] is True
            done = client.post(f"/api/transfers/{pending['id']}/complete", headers=managed)
            assert done.status_code == 200, done.text[:200]
            url = client.get(f"/api/d/{pending['id']}").json()["files"][0]["url"]
            assert httpx.get(url, timeout=60).content == content
            results["browser_post_sse_kms_and_presigned_get"] = "passed"

            api_key = session.client("apigateway").get_api_key(
                apiKey=STATE["plugin_key_id"], includeValue=True
            )["value"]
            plugin = client.post(
                "/api/plugin/transfers",
                headers={"X-API-Key": api_key},
                json={"text": "plugin kms check", "user_approved": True, "expires_in": 300},
            )
            assert plugin.status_code == 200, (plugin.status_code, plugin.text[:200])
            plugin = plugin.json()
            ids.append((plugin["id"], {"X-Manage-Token": plugin["manage_token"]}))
            head = s3.head_object(Bucket=STATE["files_bucket"], Key=f"transfers/{plugin['id']}/0")
            assert head["ServerSideEncryption"] == "aws:kms"
            assert (
                httpx.get(
                    client.get(f"/api/d/{plugin['id']}").json()["files"][0]["url"], timeout=60
                ).content
                == b"plugin kms check"
            )
            results["plugin_transfer_sse_kms"] = "passed"
        finally:
            for ident, headers in ids:
                client.delete(f"/api/transfers/{ident}", headers=headers)

    # WAF: no blocked requests from our traffic (sampled requests are best-effort).
    end = datetime.now(timezone.utc)
    blocked = 0
    for name in (
        "RateLimitPerIp",
        "AWSManagedRulesCommonRuleSet",
        "AWSManagedRulesKnownBadInputsRuleSet",
        "AWSManagedRulesAmazonIpReputationList",
    ):
        for metric in cw.list_metrics(
            Namespace="AWS/WAFV2",
            MetricName="BlockedRequests",
            Dimensions=[
                {"Name": "WebACL", "Value": "securedrop-api-acl"},
                {"Name": "Rule", "Value": name},
            ],
        )["Metrics"]:
            points = cw.get_metric_statistics(
                Namespace="AWS/WAFV2",
                MetricName="BlockedRequests",
                Dimensions=metric["Dimensions"],
                StartTime=end - timedelta(hours=1),
                EndTime=end,
                Period=3600,
                Statistics=["Sum"],
            )["Datapoints"]
            blocked += int(sum(p["Sum"] for p in points))
    results["waf_blocked_requests_last_hour"] = blocked
    print(json.dumps(results, indent=2))
    assert blocked == 0, "WAF blocked requests; inspect sampled requests"


if __name__ == "__main__":
    main()
