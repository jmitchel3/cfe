from unittest import mock

import pytest

from cfe.stack import StackConfigError, loader
from cfe.stack.modules.local_storage import LocalStorage
from cfe.stack.modules.s3 import S3
from tests.stack.helpers import apply_stack

SECRET = "sup3r-s3cret-value"
BASE = {"S3_BUCKET": "media", "S3_ACCESS_KEY_ID": "AKIAEXAMPLE", "S3_SECRET_ACCESS_KEY": SECRET}


def configure(modules, environ, ns=None):
    ns = {} if ns is None else ns
    ns = apply_stack(modules, environ, ns)
    return ns


def s3_env(**extra):
    env, missing = loader.resolve_env(S3(), {**BASE, **extra})
    assert missing == []
    return env


def test_s3_aws_defaults_and_staticfiles_kept():
    ns = configure(
        "s3",
        BASE,
        ns={"STORAGES": {"staticfiles": {"BACKEND": "whitenoise.Storage"}}},
    )
    assert ns["STORAGES"]["staticfiles"] == {"BACKEND": "whitenoise.Storage"}
    default = ns["STORAGES"]["default"]
    assert default["BACKEND"] == "storages.backends.s3.S3Storage"
    opts = default["OPTIONS"]
    assert opts["bucket_name"] == "media"
    assert opts["region_name"] == "us-east-1"
    assert opts["endpoint_url"] is None
    assert opts["querystring_auth"] is True
    assert opts["location"] == ""
    cfg = opts["client_config"]
    assert cfg.s3 == {"addressing_style": None}
    assert cfg.signature_version == "s3v4"
    assert cfg.request_checksum_calculation is None
    assert not any(k.startswith("AWS_") for k in ns)


def test_s3_r2_endpoint_defaults():
    endpoint = "https://acct.r2.cloudflarestorage.com"
    ns = configure(
        "s3", {**BASE, "S3_ENDPOINT_URL": endpoint, "S3_CUSTOM_DOMAIN": "cdn.example.com"}
    )
    opts = ns["STORAGES"]["default"]["OPTIONS"]
    assert opts["endpoint_url"] == endpoint
    assert opts["region_name"] == "auto"
    assert opts["custom_domain"] == "cdn.example.com"
    cfg = opts["client_config"]
    assert cfg.s3 == {"addressing_style": "path"}
    assert cfg.request_checksum_calculation == "when_required"
    assert cfg.response_checksum_validation == "when_required"


def test_s3_minio_overrides():
    ns = configure(
        "s3",
        {
            **BASE,
            "S3_ENDPOINT_URL": "http://localhost:9000",
            "S3_REGION": "us-west-2",
            "S3_ADDRESSING_STYLE": "virtual",
            "S3_PUBLIC": "true",
            "S3_LOCATION": "uploads",
        },
    )
    opts = ns["STORAGES"]["default"]["OPTIONS"]
    assert opts["region_name"] == "us-west-2"
    assert opts["client_config"].s3 == {"addressing_style": "virtual"}
    assert opts["querystring_auth"] is False
    assert opts["location"] == "uploads"


def test_s3_aws_alias_env_vars():
    ns = configure(
        "s3", {"S3_BUCKET": "b", "AWS_ACCESS_KEY_ID": "id", "AWS_SECRET_ACCESS_KEY": SECRET}
    )
    opts = ns["STORAGES"]["default"]["OPTIONS"]
    assert opts["access_key"] == "id"
    assert opts["secret_key"] == SECRET


def test_s3_missing_required_names_only():
    with pytest.raises(StackConfigError) as exc:
        configure("s3", {"S3_SECRET_ACCESS_KEY": SECRET})
    msg = str(exc.value)
    assert "S3_BUCKET" in msg and "S3_ACCESS_KEY_ID" in msg
    assert SECRET not in msg


def test_s3_storage_instantiates():
    from django.utils.module_loading import import_string

    ns = configure("s3", {**BASE, "S3_ENDPOINT_URL": "http://localhost:9000"})
    default = ns["STORAGES"]["default"]
    storage = import_string(default["BACKEND"])(**default["OPTIONS"])
    assert storage.endpoint_url == "http://localhost:9000"
    assert storage.client_config.request_checksum_calculation == "when_required"


def test_s3_checks_ok():
    client = mock.Mock()
    with mock.patch("boto3.client", return_value=client) as factory:
        assert S3().checks(s3_env(S3_ENDPOINT_URL="http://localhost:9000")) == []
    client.head_bucket.assert_called_once_with(Bucket="media")
    kwargs = factory.call_args.kwargs
    assert kwargs["endpoint_url"] == "http://localhost:9000"
    assert kwargs["region_name"] == "auto"


def test_s3_checks_failure_hides_secret():
    from botocore.exceptions import ClientError

    client = mock.Mock()
    client.head_bucket.side_effect = ClientError(
        {"Error": {"Code": "403", "Message": f"denied for {SECRET}"}}, "HeadBucket"
    )
    with mock.patch("boto3.client", return_value=client):
        problems = S3().checks(s3_env())
    assert problems == ["S3 bucket 'media' not reachable (ClientError 403)"]

    client.head_bucket.side_effect = ConnectionError(SECRET)
    with mock.patch("boto3.client", return_value=client):
        problems = S3().checks(s3_env())
    assert problems == ["S3 bucket 'media' not reachable (ConnectionError)"]


def test_local_defaults():
    ns = configure("local", {}, ns={"STORAGES": {"staticfiles": {"BACKEND": "x"}}})
    assert ns["MEDIA_ROOT"] == "media"
    assert ns["MEDIA_URL"] == "/media/"
    assert ns["STORAGES"] == {
        "staticfiles": {"BACKEND": "x"},
        "default": {
            "BACKEND": "django.core.files.storage.FileSystemStorage",
            "OPTIONS": {"location": "media", "base_url": "/media/"},
        },
    }


def test_local_env_overrides():
    ns = configure("local", {"MEDIA_ROOT": "/srv/media", "MEDIA_URL": "/uploads/"})
    assert ns["MEDIA_ROOT"] == "/srv/media"
    assert ns["STORAGES"]["default"]["OPTIONS"]["base_url"] == "/uploads/"


def test_only_one_storage_provider():
    with pytest.raises(StackConfigError, match="storage"):
        configure("s3,local", BASE)


def test_local_module_name():
    assert LocalStorage.name == "local"
