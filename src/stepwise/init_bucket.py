"""Explicit provisioning for the isolated local MinIO demo, not API startup."""
from botocore.exceptions import ClientError

from stepwise.s3_storage import S3ArtifactStore
from stepwise.settings import get_settings


def main() -> None:
    store = S3ArtifactStore(get_settings())
    try:
        store.client.create_bucket(Bucket=store.bucket)
    except ClientError as exc:
        if exc.response["Error"]["Code"] not in {"BucketAlreadyOwnedByYou"}:
            raise
    store.check()


if __name__ == "__main__":
    main()
