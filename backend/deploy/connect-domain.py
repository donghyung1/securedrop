"""Connect a Route 53 domain to the existing deployment using AWS CLI."""

import argparse
import hashlib
import json
import runpy
import subprocess
import time
from pathlib import Path

DEPLOY = runpy.run_path(str(Path(__file__).with_name("deploy-aws.py")))
aws, save, state = DEPLOY["aws"], DEPLOY["save"], DEPLOY["STATE"]


def wait_for(description, check, seconds=1200):
    print(description, flush=True)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(15)
    raise RuntimeError(description + " timed out; rerun to resume")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--hosted-zone-id", required=True)
    args = parser.parse_args()
    domain = args.domain.lower().rstrip(".")
    zone = aws("route53", "get-hosted-zone", id=args.hosted_zone_id)["HostedZone"]
    suffix = zone["Name"].rstrip(".")
    if zone["Config"]["PrivateZone"] or not domain.endswith("." + suffix):
        raise RuntimeError("Domain must belong to the selected public hosted zone")

    certificates = aws("acm", "list-certificates", region="us-east-1")
    matches = [
        x
        for x in certificates["CertificateSummaryList"]
        if x["DomainName"] == domain and x["Status"] in ("ISSUED", "PENDING_VALIDATION")
    ]
    if matches:
        arn = matches[0]["CertificateArn"]
    else:
        arn = aws(
            "acm",
            "request-certificate",
            region="us-east-1",
            domain_name=domain,
            validation_method="DNS",
            idempotency_token=hashlib.sha256(domain.encode()).hexdigest()[:32],
            tags=[{"Key": "Project", "Value": "SecureDropCloud"}],
        )["CertificateArn"]
    save("custom_domain", domain)
    save("hosted_zone_id", args.hosted_zone_id)
    save("certificate_arn", arn)

    def certificate():
        return aws("acm", "describe-certificate", region="us-east-1", certificate_arn=arn)[
            "Certificate"
        ]

    def validation_record():
        for option in certificate().get("DomainValidationOptions", []):
            if "ResourceRecord" in option:
                return option["ResourceRecord"]
        return None

    record = wait_for("Waiting for certificate validation record...", validation_record, 120)
    aws(
        "route53",
        "change-resource-record-sets",
        hosted_zone_id=args.hosted_zone_id,
        change_batch={
            "Changes": [
                {
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": record["Name"],
                        "Type": record["Type"],
                        "TTL": 300,
                        "ResourceRecords": [{"Value": record["Value"]}],
                    },
                }
            ]
        },
    )

    def issued():
        status = certificate()["Status"]
        if status not in ("PENDING_VALIDATION", "ISSUED"):
            raise RuntimeError("Certificate status: " + status)
        return status == "ISSUED"

    wait_for("Waiting for DNS validation and certificate issuance...", issued)
    existing = aws("cloudfront", "get-distribution-config", id=state["distribution_id"])
    config = existing["DistributionConfig"]
    aliases = list(dict.fromkeys(config["Aliases"].get("Items", []) + [domain]))
    config["Aliases"] = {"Quantity": len(aliases), "Items": aliases}
    config["ViewerCertificate"] = {
        "ACMCertificateArn": arn,
        "SSLSupportMethod": "sni-only",
        "MinimumProtocolVersion": "TLSv1.2_2021",
    }
    aws(
        "cloudfront",
        "update-distribution",
        id=state["distribution_id"],
        if_match=existing["ETag"],
        distribution_config=config,
    )
    distribution = aws("cloudfront", "get-distribution", id=state["distribution_id"])[
        "Distribution"
    ]
    cloudfront_url = "https://" + distribution["DomainName"]
    origins = list(dict.fromkeys(["https://" + domain, cloudfront_url]))
    command = aws(
        "ssm",
        "send-command",
        instance_ids=[state["instance_id"]],
        document_name="AWS-RunShellScript",
        parameters={
            "commands": [
                "set -eu",
                "sed -i '/^ALLOWED_ORIGINS=/d' /opt/securedrop/.env",
                "printf '%s\\n' 'ALLOWED_ORIGINS="
                + json.dumps(origins)
                + "' >> /opt/securedrop/.env",
                "sed -i '/^PUBLIC_BASE_URL=/d' /opt/securedrop/.env",
                "printf '%s\\n' 'PUBLIC_BASE_URL=https://" + domain + "' >> /opt/securedrop/.env",
                "systemctl restart securedrop",
                "sleep 3",
                "curl --fail --silent http://127.0.0.1:8000/api/health",
            ],
            "executionTimeout": ["60"],
        },
    )
    command_id = command["Command"]["CommandId"]
    save("domain_command_id", command_id)

    def configured():
        invocation = aws(
            "ssm", "get-command-invocation", command_id=command_id, instance_id=state["instance_id"]
        )
        status = invocation["Status"]
        if status not in ("Pending", "InProgress", "Delayed", "Success"):
            raise RuntimeError("SSM failed; inspect command " + command_id)
        return status == "Success"

    wait_for("Waiting for app origin configuration...", configured, 120)
    wait_for(
        "Waiting for CloudFront certificate and domain deployment...",
        lambda: (
            aws("cloudfront", "get-distribution", id=state["distribution_id"])["Distribution"][
                "Status"
            ]
            == "Deployed"
        ),
    )
    changes = [
        {
            "Action": "UPSERT",
            "ResourceRecordSet": {
                "Name": domain,
                "Type": kind,
                "AliasTarget": {
                    "HostedZoneId": "Z2FDTNDATAQYW2",
                    "DNSName": distribution["DomainName"],
                    "EvaluateTargetHealth": False,
                },
            },
        }
        for kind in ("A", "AAAA")
    ]
    change = aws(
        "route53",
        "change-resource-record-sets",
        hosted_zone_id=args.hosted_zone_id,
        change_batch={"Changes": changes},
    )
    wait_for(
        "Waiting for Route 53 DNS publication...",
        lambda: (
            aws("route53", "get-change", id=change["ChangeInfo"]["Id"])["ChangeInfo"]["Status"]
            == "INSYNC"
        ),
        300,
    )
    save("cloudfront_url", cloudfront_url)
    save("url", "https://" + domain)
    subprocess.run(
        ["uv", "run", "python", "deploy/verify_https.py"], cwd=DEPLOY["ROOT"], check=True
    )
    print("Custom domain verified: " + state["url"], flush=True)


if __name__ == "__main__":
    main()
