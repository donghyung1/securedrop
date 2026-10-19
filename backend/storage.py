import base64
from functools import cached_property
from io import BytesIO
from urllib.parse import quote

import boto3
import qrcode
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from errors import APIError


class Storage:
    def __init__(self, settings):
        self.settings = settings

    @cached_property
    def client(self):
        return boto3.Session(
            profile_name=self.settings.aws_profile or None,
            region_name=self.settings.aws_region,
        ).client(
            "s3",
            endpoint_url=f"https://s3.{self.settings.aws_region}.amazonaws.com",
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "virtual"},
                retries={"max_attempts": 2},
            ),
        )

    def create(self, key: str, content: bytes, filename: str, expires: int) -> str:
        if not self.settings.s3_bucket:
            raise APIError(502, "STORAGE_ERROR", "S3_BUCKET 설정이 필요합니다.")
        try:
            # Sign before upload: signing errors cannot leave an orphaned object.
            url = self.client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.settings.s3_bucket,
                    "Key": key,
                    "ResponseContentDisposition": f"attachment; filename*=UTF-8''{quote(filename)}",
                    "ResponseContentType": "application/octet-stream",
                },
                ExpiresIn=expires,
            )
            self.client.put_object(
                Bucket=self.settings.s3_bucket,
                Key=key,
                Body=content,
                ContentType="application/octet-stream",
                ServerSideEncryption="AES256",
                CacheControl="no-store",
            )
            return url
        except (BotoCoreError, ClientError) as exc:
            raise APIError(502, "STORAGE_ERROR", "파일 저장소에 접근할 수 없습니다.") from exc

    def delete(self, key: str):
        try:
            self.client.delete_object(Bucket=self.settings.s3_bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise APIError(502, "STORAGE_ERROR", "파일을 폐기하지 못했습니다.") from exc


def qr_data(url: str) -> str:
    output = BytesIO()
    qrcode.make(url).save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
