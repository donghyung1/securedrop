import base64
import hashlib
import json

import pytest

from config import Settings
from infra.configure_security import (
    COMMON_COUNT_RULES,
    RATE_LIMIT,
    event_selectors,
    key_policy,
    trail_bucket_policy,
    waf_rules,
)
from storage import Storage

KEY_ARN = "arn:aws:kms:ap-northeast-2:111122223333:key/abcd-1234"


@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("AWS_PROFILE", raising=False)


def post(kms_key_id):
    settings = Settings(
        _env_file=None, s3_bucket="b", kms_key_id=kms_key_id, public_base_url="https://x.example"
    )
    item = {
        "type": "text/plain",
        "sha256": hashlib.sha256(b"hello").hexdigest(),
        "s3_key": "transfers/t/0",
        "size": 5,
    }
    result = Storage(settings).upload_post(item)
    policy = json.loads(base64.b64decode(result["fields"]["policy"]))
    return result["fields"], policy["conditions"]


def test_upload_post_defaults_to_sse_s3(aws_env):
    fields, conditions = post("")
    assert fields["x-amz-server-side-encryption"] == "AES256"
    assert "x-amz-server-side-encryption-aws-kms-key-id" not in fields
    assert {"x-amz-server-side-encryption": "AES256"} in conditions


def test_upload_post_pins_sse_kms_and_key(aws_env):
    fields, conditions = post(KEY_ARN)
    assert fields["x-amz-server-side-encryption"] == "aws:kms"
    assert fields["x-amz-server-side-encryption-aws-kms-key-id"] == KEY_ARN
    assert {"x-amz-server-side-encryption": "aws:kms"} in conditions
    assert {"x-amz-server-side-encryption-aws-kms-key-id": KEY_ARN} in conditions


def test_key_policy_limits_lambda_to_s3_data_key_actions():
    role = "arn:aws:iam::111122223333:role/securedrop-lambda-v13"
    policy = key_policy("111122223333", role, "files-bucket", "ap-northeast-2")
    admin, lam = policy["Statement"]
    assert admin["Principal"] == {"AWS": "arn:aws:iam::111122223333:root"}
    assert lam["Principal"] == {"AWS": role}
    assert sorted(lam["Action"]) == ["kms:Decrypt", "kms:GenerateDataKey"]
    assert lam["Condition"]["StringEquals"]["kms:ViaService"] == "s3.ap-northeast-2.amazonaws.com"
    assert (
        "arn:aws:s3:::files-bucket"
        in lam["Condition"]["StringLike"]["kms:EncryptionContext:aws:s3:arn"]
    )


def test_waf_rules_count_body_rules_and_rate_limit_per_ip():
    rules = {r["Name"]: r for r in waf_rules()}
    assert set(rules) == {
        "RateLimitPerIp",
        "AWSManagedRulesAmazonIpReputationList",
        "AWSManagedRulesKnownBadInputsRuleSet",
        "AWSManagedRulesCommonRuleSet",
    }
    rate = rules["RateLimitPerIp"]["Statement"]["RateBasedStatement"]
    assert rate["AggregateKeyType"] == "IP" and rate["Limit"] == RATE_LIMIT
    common = rules["AWSManagedRulesCommonRuleSet"]["Statement"]["ManagedRuleGroupStatement"]
    assert "SizeRestrictions_BODY" in COMMON_COUNT_RULES
    assert {o["Name"] for o in common["RuleActionOverrides"]} == set(COMMON_COUNT_RULES)
    assert all(o["ActionToUse"] == {"Count": {}} for o in common["RuleActionOverrides"])
    priorities = [r["Priority"] for r in rules.values()]
    assert len(set(priorities)) == len(priorities)


def test_trail_bucket_policy_scopes_cloudtrail_and_denies_plain_http():
    arn = "arn:aws:cloudtrail:ap-northeast-2:111122223333:trail/securedrop-trail"
    statements = trail_bucket_policy("logs", "111122223333", arn)["Statement"]
    for s in statements[:2]:
        assert s["Condition"] == {"StringEquals": {"aws:SourceArn": arn}}
    assert statements[1]["Resource"] == "arn:aws:s3:::logs/AWSLogs/111122223333/*"
    assert statements[2]["Condition"] == {"Bool": {"aws:SecureTransport": "false"}}


def test_event_selectors_limit_data_events_to_transfer_prefix():
    management, data = event_selectors("files-bucket")
    assert management["FieldSelectors"] == [{"Field": "eventCategory", "Equals": ["Management"]}]
    assert {
        "Field": "resources.ARN",
        "StartsWith": ["arn:aws:s3:::files-bucket/transfers/"],
    } in data["FieldSelectors"]
