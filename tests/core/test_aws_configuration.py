"""Default credential discovery works without changing the MinIO implementation."""
from unittest.mock import Mock, patch

from stepwise.s3_storage import S3ArtifactStore
from stepwise.settings import Settings


def test_aws_uses_default_endpoint_and_credential_chain():
    with patch("stepwise.s3_storage.boto3.client", return_value=Mock()) as create:
        S3ArtifactStore(Settings(_env_file=None, s3_endpoint_url=None,
                                s3_access_key="", s3_secret_key="", s3_region="eu-west-1"))
    kwargs = create.call_args.kwargs
    assert kwargs["endpoint_url"] is None
    assert kwargs["region_name"] == "eu-west-1"
    assert kwargs["aws_access_key_id"] is None
    assert kwargs["aws_secret_access_key"] is None
