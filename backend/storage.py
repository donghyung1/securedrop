import base64
import hashlib
from functools import cached_property
from io import BytesIO
from urllib.parse import quote

import boto3
import segno
from botocore.config import Config
from botocore.exceptions import ClientError

from errors import APIError
from store import aws_errors


class Storage:
    def __init__(self, settings):
        self.settings = settings

    @cached_property
    def client(self):
        return boto3.Session(
            profile_name=self.settings.aws_profile,
            region_name=self.settings.aws_region,
        ).client("s3", config=Config(signature_version="s3v4", retries={"max_attempts": 2}))

    @aws_errors
    def upload_post(self, item):
        # Pin the checksum in the signed policy so reusing a POST cannot replace
        # a verified object with different bytes after /complete.
        checksum = base64.b64encode(bytes.fromhex(item["sha256"])).decode()
        fields = {
            "Content-Type": item["type"],
            "x-amz-server-side-encryption": "AES256",
            "x-amz-checksum-algorithm": "SHA256",
            "x-amz-checksum-sha256": checksum,
        }
        return self.client.generate_presigned_post(
            Bucket=self.settings.s3_bucket,
            Key=item["s3_key"],
            Fields=fields,
            Conditions=[
                {"key": item["s3_key"]},
                ["content-length-range", 1, item["size"]],
                *[{k: v} for k, v in fields.items()],
            ],
            ExpiresIn=self.settings.upload_url_ttl_sec,
        )

    @aws_errors
    def verify(self, item):
        try:
            head = self.client.head_object(Bucket=self.settings.s3_bucket, Key=item["s3_key"])
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                raise APIError(409, "UPLOAD_INCOMPLETE", "업로드가 완료되지 않았습니다.") from exc
            raise
        if head["ContentLength"] != item["size"]:
            raise APIError(409, "UPLOAD_INCOMPLETE", "업로드 파일 크기가 다릅니다.")
        body = self.client.get_object(Bucket=self.settings.s3_bucket, Key=item["s3_key"])["Body"]
        digest = hashlib.sha256()
        try:
            for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                digest.update(chunk)
        finally:
            body.close()
        if digest.hexdigest() != item["sha256"]:
            raise APIError(409, "INTEGRITY_MISMATCH", "검사한 파일과 업로드 파일이 다릅니다.")

    @aws_errors
    def download_url(self, item, expires):
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.settings.s3_bucket,
                "Key": item["s3_key"],
                "ResponseContentDisposition": "attachment; filename*=UTF-8''"
                + quote(item["name"], safe=""),
            },
            ExpiresIn=expires,
        )

    @aws_errors
    def create(self, item, content):
        self.client.put_object(
            Bucket=self.settings.s3_bucket,
            Key=item["s3_key"],
            Body=content,
            ContentType=item["type"],
            ServerSideEncryption="AES256",
        )

    @aws_errors
    def delete(self, key):
        self.client.delete_object(Bucket=self.settings.s3_bucket, Key=key)


def qr_data(url):
    output = BytesIO()
    segno.make(url, micro=False).save(output, kind="png", scale=4, border=4)
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()
