import base64
from functools import cached_property
from io import BytesIO
from urllib.parse import quote

import boto3
import qrcode
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image

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

    def download_url(self, key: str, expires: int) -> str:
        if not self.settings.s3_bucket:
            raise APIError(502, "STORAGE_ERROR", "S3_BUCKET 설정이 필요합니다.")
        try:
            return self.client.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": self.settings.s3_bucket,
                    "Key": key,
                },
                ExpiresIn=expires,
            )
        except (BotoCoreError, ClientError) as exc:
            raise APIError(502, "STORAGE_ERROR", "다운로드 링크를 생성하지 못했습니다.") from exc

    def create(self, key: str, content: bytes, filename: str, expires: int) -> str:
        # Sign before upload: signing errors cannot leave an orphaned object.
        url = self.download_url(key, expires)
        try:
            self.client.put_object(
                Bucket=self.settings.s3_bucket,
                Key=key,
                Body=content,
                ContentType="application/octet-stream",
                # Keep filenames out of the signed URL: percent-encoding long
                # Unicode names twice can exceed the QR code's payload capacity.
                ContentDisposition=f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
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
    # Use whole pixels per module and pad to 180px rather than resampling edges.
    qr = qrcode.QRCode(box_size=4, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    qr.box_size = max(1, 180 // (qr.modules_count + 2 * qr.border))
    image = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    size = max(180, image.width)
    canvas = Image.new("RGB", (size, size), "white")
    offset = (size - image.width) // 2
    canvas.paste(image, (offset, offset))
    canvas.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
