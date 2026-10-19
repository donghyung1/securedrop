"""Deploy the demo with AWS CLI; keep resource IDs and secrets in ignored deploy/aws/."""

import base64
import json
import os
import secrets
import subprocess
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "deploy" / "aws"
CONFIG = json.loads((OUT / "resources.json").read_text())
STATE_PATH = OUT / "deployment.json"
STATE = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
REGION = CONFIG["region"]


def save(key, value):
    STATE[key] = value
    STATE_PATH.write_text(json.dumps(STATE, indent=2) + "\n", encoding="utf-8")
    return value


def aws(service, operation, **kwargs):
    region = kwargs.pop("region", REGION)
    args = [
        "aws",
        service,
        operation,
        "--region",
        region,
        "--profile",
        CONFIG["profile"],
        "--output",
        "json",
        "--no-cli-pager",
    ]
    for key, value in kwargs.items():
        args.append("--" + key.replace("_", "-"))
        if isinstance(value, (dict, list)):
            path = OUT / f"request-{service}-{operation}-{key}.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            args.append("file://" + path.as_posix())
        elif value is not True:
            args.append(str(value))
    result = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "AWS_PAGER": ""},
    )
    if result.returncode:
        raise RuntimeError(f"{service} {operation}: {result.stderr.strip()}")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def tags(name):
    return [
        {"Key": "Name", "Value": name},
        {"Key": "Project", "Value": "SecureDropCloud"},
        {"Key": "ManagedBy", "Value": "AWSCLI"},
    ]


def main():
    identity = aws("sts", "get-caller-identity")
    if identity["Account"] != CONFIG["account_id"]:
        raise RuntimeError("Configured AWS account differs from current identity")
    if "vpc_id" not in STATE:
        save(
            "vpc_id",
            aws(
                "ec2",
                "create-vpc",
                cidr_block="10.42.0.0/16",
                tag_specifications=[{"ResourceType": "vpc", "Tags": tags("securedrop")}],
            )["Vpc"]["VpcId"],
        )
    vpc = STATE["vpc_id"]
    aws("ec2", "modify-vpc-attribute", vpc_id=vpc, enable_dns_support={"Value": True})
    aws("ec2", "modify-vpc-attribute", vpc_id=vpc, enable_dns_hostnames={"Value": True})
    if "subnet_id" not in STATE:
        save(
            "subnet_id",
            aws(
                "ec2",
                "create-subnet",
                vpc_id=vpc,
                cidr_block="10.42.1.0/24",
                tag_specifications=[{"ResourceType": "subnet", "Tags": tags("securedrop")}],
            )["Subnet"]["SubnetId"],
        )
    if "gateway_id" not in STATE:
        save(
            "gateway_id",
            aws(
                "ec2",
                "create-internet-gateway",
                tag_specifications=[
                    {"ResourceType": "internet-gateway", "Tags": tags("securedrop")}
                ],
            )["InternetGateway"]["InternetGatewayId"],
        )
        aws("ec2", "attach-internet-gateway", internet_gateway_id=STATE["gateway_id"], vpc_id=vpc)
    if "route_table_id" not in STATE:
        save(
            "route_table_id",
            aws("ec2", "create-route-table", vpc_id=vpc)["RouteTable"]["RouteTableId"],
        )
        aws(
            "ec2",
            "create-route",
            route_table_id=STATE["route_table_id"],
            destination_cidr_block="0.0.0.0/0",
            gateway_id=STATE["gateway_id"],
        )
        aws(
            "ec2",
            "associate-route-table",
            route_table_id=STATE["route_table_id"],
            subnet_id=STATE["subnet_id"],
        )
    if "security_group_id" not in STATE:
        save(
            "security_group_id",
            aws(
                "ec2",
                "create-security-group",
                vpc_id=vpc,
                group_name="securedrop-cloudfront",
                description="CloudFront HTTP only; no SSH",
            )["GroupId"],
        )
        prefix = aws(
            "ec2",
            "describe-managed-prefix-lists",
            filters=[
                {
                    "Name": "prefix-list-name",
                    "Values": ["com.amazonaws.global.cloudfront.origin-facing"],
                }
            ],
        )["PrefixLists"][0]["PrefixListId"]
        aws(
            "ec2",
            "authorize-security-group-ingress",
            group_id=STATE["security_group_id"],
            ip_permissions=[
                {
                    "IpProtocol": "tcp",
                    "FromPort": 80,
                    "ToPort": 80,
                    "PrefixListIds": [{"PrefixListId": prefix}],
                }
            ],
        )
    aws(
        "iam",
        "attach-role-policy",
        role_name=CONFIG["instance_profile"],
        policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
    )
    if "origin_secret" not in STATE:
        save("origin_secret", secrets.token_hex(32))
    if "artifact_key" not in STATE:
        save("artifact_key", "transfers/_deploy/" + secrets.token_hex(16) + ".tar.gz")
    subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--output-file",
            str(OUT / "requirements.txt"),
        ],
        cwd=ROOT,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    with tarfile.open(OUT / "app.tar.gz", "w:gz") as archive:
        for path in ROOT.glob("*.py"):
            archive.add(path, arcname=path.name)
        archive.add(ROOT / "frontend", arcname="frontend")
        archive.add(OUT / "requirements.txt", arcname="requirements.txt")
    aws(
        "s3api",
        "put-object",
        bucket=CONFIG["bucket"],
        key=STATE["artifact_key"],
        body=str(OUT / "app.tar.gz"),
        server_side_encryption="AES256",
    )
    if "instance_id" not in STATE:
        image = aws(
            "ssm",
            "get-parameter",
            name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
        )
        bootstrap = (Path(__file__).parent / "user-data.sh").read_text()
        bootstrap = bootstrap.replace("__BUCKET__", CONFIG["bucket"])
        bootstrap = bootstrap.replace("__ARTIFACT__", STATE["artifact_key"])
        bootstrap = bootstrap.replace("__REGION__", REGION)
        bootstrap = bootstrap.replace("__ORIGIN_SECRET__", STATE["origin_secret"])
        result = aws(
            "ec2",
            "run-instances",
            image_id=image["Parameter"]["Value"],
            instance_type="t3.micro",
            count=1,
            iam_instance_profile={"Name": CONFIG["instance_profile"]},
            network_interfaces=[
                {
                    "DeviceIndex": 0,
                    "SubnetId": STATE["subnet_id"],
                    "Groups": [STATE["security_group_id"]],
                    "AssociatePublicIpAddress": True,
                }
            ],
            metadata_options={"HttpTokens": "required", "HttpEndpoint": "enabled"},
            block_device_mappings=[
                {
                    "DeviceName": "/dev/xvda",
                    "Ebs": {
                        "VolumeSize": 12,
                        "VolumeType": "gp3",
                        "Encrypted": True,
                        "DeleteOnTermination": True,
                    },
                }
            ],
            tag_specifications=[
                {"ResourceType": "instance", "Tags": tags("securedrop-backend")},
                {"ResourceType": "volume", "Tags": tags("securedrop-data")},
            ],
            client_token="securedrop-" + STATE["vpc_id"],
            user_data=base64.b64encode(bootstrap.encode()).decode(),
        )
        save("instance_id", result["Instances"][0]["InstanceId"])
    if "allocation_id" not in STATE:
        address = aws(
            "ec2",
            "allocate-address",
            domain="vpc",
            tag_specifications=[{"ResourceType": "elastic-ip", "Tags": tags("securedrop")}],
        )
        save("allocation_id", address["AllocationId"])
        save("public_ip", address["PublicIp"])
    while True:
        status = aws("ec2", "describe-instances", instance_ids=[STATE["instance_id"]])
        phase = status["Reservations"][0]["Instances"][0]["State"]["Name"]
        if phase == "running":
            break
        if phase != "pending":
            raise RuntimeError(f"Instance state prevents deployment: {phase}")
        time.sleep(5)
    aws(
        "ec2",
        "associate-address",
        instance_id=STATE["instance_id"],
        allocation_id=STATE["allocation_id"],
    )
    instance = aws("ec2", "describe-instances", instance_ids=[STATE["instance_id"]])
    hostname = instance["Reservations"][0]["Instances"][0]["PublicDnsName"]
    if not hostname:
        raise RuntimeError("Instance public DNS is not ready; rerun deployment")
    if "distribution_id" not in STATE:
        result = aws(
            "cloudfront",
            "create-distribution",
            distribution_config={
                "CallerReference": STATE["instance_id"],
                "Comment": "SecureDrop Cloud demo",
                "Enabled": True,
                "PriceClass": "PriceClass_All",
                "HttpVersion": "http2",
                "IsIPV6Enabled": True,
                "Origins": {
                    "Quantity": 1,
                    "Items": [
                        {
                            "Id": "backend",
                            "DomainName": hostname,
                            "CustomHeaders": {
                                "Quantity": 1,
                                "Items": [
                                    {
                                        "HeaderName": "X-Origin-Verify",
                                        "HeaderValue": STATE["origin_secret"],
                                    }
                                ],
                            },
                            "CustomOriginConfig": {
                                "HTTPPort": 80,
                                "HTTPSPort": 443,
                                "OriginProtocolPolicy": "http-only",
                                "OriginSslProtocols": {"Quantity": 1, "Items": ["TLSv1.2"]},
                                "OriginReadTimeout": 60,
                            },
                        }
                    ],
                },
                "DefaultCacheBehavior": {
                    "TargetOriginId": "backend",
                    "ViewerProtocolPolicy": "redirect-to-https",
                    "Compress": True,
                    "AllowedMethods": {
                        "Quantity": 7,
                        "Items": ["GET", "HEAD", "OPTIONS", "PUT", "PATCH", "POST", "DELETE"],
                        "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
                    },
                    "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",
                    "OriginRequestPolicyId": "216adef6-5c7f-47e4-b989-5492eafa07d3",
                    "TrustedSigners": {"Enabled": False, "Quantity": 0},
                },
                "ViewerCertificate": {"CloudFrontDefaultCertificate": True},
                "CustomErrorResponses": {
                    "Quantity": 2,
                    "Items": [
                        {"ErrorCode": 502, "ErrorCachingMinTTL": 0},
                        {"ErrorCode": 503, "ErrorCachingMinTTL": 0},
                    ],
                },
            },
        )["Distribution"]
        save("distribution_id", result["Id"])
        save("url", "https://" + result["DomainName"])
    print(
        json.dumps({k: v for k, v in STATE.items() if k != "origin_secret"}, indent=2), flush=True
    )
    print("Waiting for Systems Manager registration and instance bootstrap...", flush=True)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        info = aws(
            "ssm",
            "describe-instance-information",
            filters=[{"Key": "InstanceIds", "Values": [STATE["instance_id"]]}],
        )
        if any(x["PingStatus"] == "Online" for x in info["InstanceInformationList"]):
            break
        time.sleep(10)
    else:
        raise RuntimeError("SSM registration timed out")
    command = aws(
        "ssm",
        "send-command",
        instance_ids=[STATE["instance_id"]],
        document_name="AWS-RunShellScript",
        parameters={
            "commands": [
                "set -eu",
                "cloud-init status --wait >/dev/null 2>&1 || test -f /opt/securedrop/.bootstrap-ready",
                "test -f /opt/securedrop/.bootstrap-ready",
                (
                    f"aws s3 cp 's3://{CONFIG['bucket']}/{STATE['artifact_key']}' "
                    f"/tmp/securedrop-update.tar.gz --region {REGION} --only-show-errors"
                ),
                "systemctl stop securedrop",
                "trap 'systemctl start securedrop' EXIT",
                "tar xzf /tmp/securedrop-update.tar.gz -C /opt/securedrop",
                (
                    "/opt/securedrop/.venv/bin/pip install --quiet --require-hashes "
                    "-r /opt/securedrop/requirements.txt"
                ),
                "chown -R securedrop:securedrop /opt/securedrop",
                "rm /tmp/securedrop-update.tar.gz",
                "sed -i '/^ALLOWED_ORIGINS=/d' /opt/securedrop/.env",
                "printf '%s\\n' 'ALLOWED_ORIGINS="
                + json.dumps(
                    list(dict.fromkeys([STATE["url"], STATE.get("cloudfront_url", STATE["url"])]))
                )
                + "' >> /opt/securedrop/.env",
                "sed -i '/^PUBLIC_BASE_URL=/d' /opt/securedrop/.env",
                "printf '%s\\n' 'PUBLIC_BASE_URL=" + STATE["url"] + "' >> /opt/securedrop/.env",
                "systemctl restart securedrop nginx",
                "sleep 3",
                "curl --fail --silent http://127.0.0.1:8000/api/health",
            ],
            "executionTimeout": ["600"],
        },
    )
    save("configure_command_id", command["Command"]["CommandId"])
    print("Configuration command submitted: " + STATE["configure_command_id"], flush=True)
    deadline = time.monotonic() + 660
    while time.monotonic() < deadline:
        invocation = aws(
            "ssm",
            "get-command-invocation",
            command_id=STATE["configure_command_id"],
            instance_id=STATE["instance_id"],
        )
        if invocation["Status"] == "Success":
            break
        if invocation["Status"] not in ("Pending", "InProgress", "Delayed"):
            raise RuntimeError(
                "Server configuration failed; inspect SSM command " + STATE["configure_command_id"]
            )
        time.sleep(10)
    else:
        raise RuntimeError("Server configuration timed out")
    aws("s3api", "delete-object", bucket=CONFIG["bucket"], key=STATE["artifact_key"])
    print("Server ready. Waiting for CloudFront distribution...", flush=True)
    deadline = time.monotonic() + 1200
    while time.monotonic() < deadline:
        distribution = aws("cloudfront", "get-distribution", id=STATE["distribution_id"])
        if distribution["Distribution"]["Status"] == "Deployed":
            break
        time.sleep(20)
    else:
        raise RuntimeError("CloudFront is still deploying; inspect saved distribution ID")
    subprocess.run(["uv", "run", "python", "deploy/verify_https.py"], cwd=ROOT, check=True)
    print("Deployment verified: " + STATE["url"], flush=True)


if __name__ == "__main__":
    main()
