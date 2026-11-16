# Storage modules (slot `storage`)

Storage modules set `STORAGES["default"]` only. The project's own `STORAGES["staticfiles"]` entry is kept.

## `s3`

S3-compatible media storage through django-storages: AWS S3, Cloudflare R2, MinIO, Tigris, Backblaze B2. Install with `pip install "cfe[s3]"`. All options live in `STORAGES["default"]["OPTIONS"]`; no global `AWS_*` settings are written.

| Env var | Default | Notes |
|---|---|---|
| `S3_BUCKET` | required | Bucket name. |
| `S3_ACCESS_KEY_ID` | required, secret | Alias `AWS_ACCESS_KEY_ID`. |
| `S3_SECRET_ACCESS_KEY` | required, secret | Alias `AWS_SECRET_ACCESS_KEY`. |
| `S3_ENDPOINT_URL` | none (AWS) | Required for R2, MinIO, Tigris, B2. |
| `S3_REGION` | `auto` with an endpoint, else `us-east-1` | |
| `S3_CUSTOM_DOMAIN` | none | CDN or public bucket domain used in URLs. |
| `S3_PUBLIC` | `false` | `false`: signed, expiring URLs. `true`: plain URLs (the bucket must allow public reads). |
| `S3_ADDRESSING_STYLE` | `path` with an endpoint | Set `virtual` if your provider needs it. |
| `S3_LOCATION` | empty | Key prefix for every file. |

When `S3_ENDPOINT_URL` is set, the boto3 client uses `request_checksum_calculation` and `response_checksum_validation` set to `when_required`. botocore 1.36+ sends CRC checksums by default, which several S3-compatible stores reject.

Examples:

```sh
# Cloudflare R2
S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
# RustFS in Docker (local dev; verified end to end)
S3_ENDPOINT_URL=http://localhost:9000
# Tigris
S3_ENDPOINT_URL=https://fly.storage.tigris.dev
# Backblaze B2
S3_ENDPOINT_URL=https://s3.<region>.backblazeb2.com S3_REGION=<region>
```

MinIO no longer publishes Docker images (Docker Hub and quay.io pulls fail), so local Docker setups use RustFS (`rustfs/rustfs`, env `RUSTFS_ACCESS_KEY`/`RUSTFS_SECRET_KEY`) or SeaweedFS. Existing MinIO servers still work with the same settings.

`cfe doctor` runs `head_bucket` and reports only the exception type and error code.

## `local`

`FileSystemStorage` on local disk. Also sets `MEDIA_ROOT` and `MEDIA_URL`.

| Env var | Default |
|---|---|
| `MEDIA_ROOT` | `media` |
| `MEDIA_URL` | `/media/` |
