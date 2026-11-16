"""S3-compatible object storage: AWS S3, Cloudflare R2, MinIO, Tigris, Backblaze B2.

Sets STORAGES["default"] only, so the project's `staticfiles` entry is kept.
Every option goes in STORAGES["default"]["OPTIONS"]; no global AWS_* settings.
"""

from typing import ClassVar

from cfe.stack.loader import to_bool
from cfe.stack.module import EnvVar, Module

BACKEND = "storages.backends.s3.S3Storage"


class S3(Module):
    name = "s3"
    label = "S3-compatible storage"
    description = "Media files on AWS S3, Cloudflare R2, MinIO, Tigris, or Backblaze B2."
    provides = ("storage",)
    python_requires: ClassVar[dict[str, str]] = {"storages": "cfe[s3]", "boto3": "cfe[s3]"}
    env = (
        EnvVar("S3_BUCKET", required=True, help="Bucket name."),
        EnvVar(
            "S3_ACCESS_KEY_ID",
            required=True,
            secret=True,
            aliases=("AWS_ACCESS_KEY_ID",),
            help="Access key ID.",
        ),
        EnvVar(
            "S3_SECRET_ACCESS_KEY",
            required=True,
            secret=True,
            aliases=("AWS_SECRET_ACCESS_KEY",),
            help="Secret access key.",
        ),
        EnvVar("S3_ENDPOINT_URL", help="Endpoint for non-AWS providers (required for R2, MinIO)."),
        EnvVar("S3_REGION", help='Region; default "auto" with an endpoint, else "us-east-1".'),
        EnvVar("S3_CUSTOM_DOMAIN", help="Domain (CDN or public bucket) used in file URLs."),
        EnvVar(
            "S3_PUBLIC", default=False, cast=to_bool, help="Unsigned public URLs (default false)."
        ),
        EnvVar("S3_ADDRESSING_STYLE", help='"path" or "virtual"; default "path" with an endpoint.'),
        EnvVar("S3_LOCATION", default="", help="Key prefix for every stored file."),
    )

    def options(self, env):
        """Keyword arguments for S3Storage and the boto3 client."""
        endpoint = env.S3_ENDPOINT_URL or None
        region = env.S3_REGION or ("auto" if endpoint else "us-east-1")
        addressing = env.S3_ADDRESSING_STYLE or ("path" if endpoint else None)
        return endpoint, region, addressing

    def client_config(self, env):
        from botocore.config import Config

        endpoint, _, addressing = self.options(env)
        kwargs = {"signature_version": "s3v4", "s3": {"addressing_style": addressing}}
        if endpoint:
            # botocore >= 1.36 sends CRC checksums by default; several
            # S3-compatible stores (R2, MinIO versions, B2) reject or mishandle
            # them. Restore the pre-1.36 behaviour for non-AWS endpoints.
            kwargs["request_checksum_calculation"] = "when_required"
            kwargs["response_checksum_validation"] = "when_required"
        return Config(**kwargs)

    def settings(self, env):
        endpoint, region, _ = self.options(env)
        options = {
            "bucket_name": env.S3_BUCKET,
            "access_key": env.S3_ACCESS_KEY_ID,
            "secret_key": env.S3_SECRET_ACCESS_KEY,
            "region_name": region,
            "endpoint_url": endpoint,
            "custom_domain": env.S3_CUSTOM_DOMAIN or None,
            "location": env.S3_LOCATION or "",
            "querystring_auth": not env.S3_PUBLIC,
            "client_config": self.client_config(env),
        }
        return {"STORAGES": {"default": {"BACKEND": BACKEND, "OPTIONS": options}}}

    def checks(self, env):
        import boto3

        endpoint, region, _ = self.options(env)
        client = boto3.client(
            "s3",
            aws_access_key_id=env.S3_ACCESS_KEY_ID,
            aws_secret_access_key=env.S3_SECRET_ACCESS_KEY,
            region_name=region,
            endpoint_url=endpoint,
            config=self.client_config(env),
        )
        try:
            client.head_bucket(Bucket=env.S3_BUCKET)
        except Exception as exc:  # noqa: BLE001 (report any failure, never its text)
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            detail = f"{type(exc).__name__} {code}" if code else type(exc).__name__
            return [f"S3 bucket {env.S3_BUCKET!r} not reachable ({detail})"]
        return []


MODULES = [S3]
