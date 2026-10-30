# AWS deployment (optional)

One Ubuntu 24.04 EC2 instance runs the existing Docker Compose application, PostgreSQL and Redis.
Only uploaded recordings and generated artifacts use private Amazon S3. The existing Boto3 backend
already accepts a region and bucket with no custom endpoint and uses the standard credential chain.
No application code, HTTP contracts, domain rules, or job semantics change.

## Prerequisites

- Bash (Linux, macOS, or WSL), AWS CLI v2, Git, SSH, and an AWS account.
- A CLI identity allowed to create/delete S3 buckets, IAM roles/inline policies/instance profiles,
  pass the instance role, launch/terminate EC2 and manage security groups; read SSM parameters,
  subnets, instances and key pairs. This operator identity needs more permissions than the app role.
- An **existing** EC2 key pair in the selected region and its private `.pem` file on your workstation.
  The scripts use it but never create, upload or delete your key pair.
- A default VPC with a public subnet, or `AWS_SUBNET_ID` for an existing public subnet with an
  Internet Gateway route. No VPC, NAT Gateway or load balancer is created.
- Push `aws-deploy` before setup so EC2 can clone its deployment files. Use `AWS_GIT_REF` to select
  another published branch/tag containing these files. Setup launches billable resources; running
  tests without the opt-in variable does not deploy anything.

Configure AWS CLI credentials on your workstation using `aws configure sso` / `aws sso login`
or your existing AWS profile. Do not place keys in the repository. All commands below run from
the repository root in Bash. `.env.example` lists placeholders; setup reads **exported variables**,
not that file.

## Setup

```bash
git switch aws-deploy
git push -u origin aws-deploy
export AWS_PROFILE=your-profile
export AWS_REGION=us-east-1
export AWS_BUCKET=your-unique-demo-bucket
export AWS_KEY_PAIR=stepwise-demo
export AWS_SSH_CIDR=your-public-ip/32
bash deploy/aws/setup.sh
```

Replace the placeholders. `AWS_REGION` defaults to `us-east-1`, `AWS_KEY_PAIR` to `stepwise-demo`,
and an omitted `AWS_BUCKET` gets a time/random name. Optional `AWS_INSTANCE_TYPE` defaults to
`t3.micro` (x86), and `AWS_GIT_REF` to `aws-deploy`. The x86 Ubuntu AMI is resolved from Canonical's
public SSM parameter. ARM instance types are not supported by this bootstrap.

Setup creates a dedicated bucket, blocks all public access, disables ACLs with BucketOwnerEnforced,
enables AES256 default encryption, and refuses an existing bucket. It creates an EC2 trust role,
an inline S3 policy and instance profile, and a security group allowing **only SSH from your /32**.
It prints resource identifiers and the SSH command. Resources are recorded under ignored
`deploy/aws/.state/`, including state needed to clean up a partially failed setup. Preserve this
directory until teardown. Use `STEPWISE_AWS_STATE` for another absolute state path if needed.

EC2 requires IMDSv2 with hop limit 2 so bridge-networked containers can obtain instance-role
credentials. Bootstrap installs Docker/Compose, adds 4 GiB swap for small-instance builds, clones
the public repository, records its commit in `/opt/stepwise-deployed-commit`, and runs:

```bash
cd /opt/stepwise
sudo docker compose -p stepwise-aws -f docker-compose.yml -f deploy/aws/compose.yml up --build -d
```

The overlay sets `STEPWISE_STORAGE_BACKEND=s3`, region, bucket and `artifacts` prefix. It omits
`STEPWISE_S3_ENDPOINT_URL` and static access/secret keys. The root Compose file is unchanged;
the overlay removes API/worker artifact mounts and binds API/web ports to host loopback.
PostgreSQL and Redis retain their Compose volumes on the encrypted, delete-on-termination root disk.
Compose must support `!override` (2.24.4+) and `!reset`; Ubuntu's installed Compose must meet this
version. Setup completion means EC2 is running, not that image builds or readiness have finished.

## Access and readiness

Use the IP printed by setup. Forward both ports (choose unused local ports if needed):

```bash
ssh -i /path/to/stepwise-demo.pem \
  -L 8000:127.0.0.1:8000 -L 5173:127.0.0.1:5173 ubuntu@INSTANCE_PUBLIC_IP
```

In the SSH session:

```bash
sudo cloud-init status --wait
sudo tail -n 60 /var/log/cloud-init-output.log
cd /opt/stepwise
sudo docker compose -p stepwise-aws -f docker-compose.yml -f deploy/aws/compose.yml ps
curl --fail http://127.0.0.1:8000/ready
```

From your workstation, open `http://localhost:5173` and `http://localhost:8000/docs` while the
tunnel remains open. This keeps the unauthenticated demo off public HTTP ports. Image builds on
`t3.micro` can be slow or memory-constrained; swap helps but is not a measured capacity guarantee.
If bootstrap fails, inspect cloud-init and Compose logs, fix the cause, then rerun the Compose
command; do not rerun the whole bootstrap (it provisions swap and clones a directory).

## Instance role policy (complete)

Replace `YOUR_BUCKET` with the dedicated bucket. Setup generates this same policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::YOUR_BUCKET"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::YOUR_BUCKET/*"
    }
  ]
}
```

There is no IAM `s3:HeadObject` action: HeadObject is authorized by `s3:GetObject`; HeadBucket
uses `s3:ListBucket`. The role has no deletion, bucket administration, KMS or other-bucket access.
AES256 SSE-S3 avoids a KMS grant. The EC2 trust policy is:

```json
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}
```

Reference: [S3 IAM operation mapping](https://docs.aws.amazon.com/AmazonS3/latest/userguide/security_iam_service-with-iam.html)
and [container IMDSv2 hop limit](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/configuring-IMDS-new-instances.html).

## Synthetic verification

On your workstation, with the SSH tunnel active and the standard AWS credential chain configured:

```bash
python -m pip install -e '.[dev]'
python scripts/aws_verify.py --api-url http://localhost:8000 \
  --bucket "$AWS_BUCKET" --region "$AWS_REGION" --output aws-verification.json
```

This submits the bundled synthetic normal-like recording with standing calibration, waits for
SUCCEEDED, downloads all five artifacts, and finds the corresponding complete attempt in S3.
For each object it compares API bytes, S3 bytes, content lengths, and the stored SHA-256 user
metadata from both GetObject and HeadObject. S3 ETags are not treated as SHA-256 digests.
No database/API changes are needed to expose private storage keys. A mismatch fails verification.
The local JSON records real results or a failure category; it omits bucket/account names and is
gitignored. It is software verification, not clinical validation. The workstation identity needs
GetObject/ListBucket on this bucket, besides the API tunnel.

For the opt-in backend integration check against an existing dedicated bucket:

```bash
export STEPWISE_TEST_AWS_BUCKET="$AWS_BUCKET"
python -m pytest tests/integration/test_aws_storage.py -q
unset STEPWISE_TEST_AWS_BUCKET
```

Without that variable the test skips, including in CI. It uses a unique `aws-integration/` prefix,
tests conditional immutable writes, readback and hash metadata, and leaves a few synthetic objects
because the instance policy deliberately has no DeleteObject. Teardown removes them. This checks
storage, while `aws_verify.py` checks the deployed asynchronous API/worker path. Avoid custom
`AWS_ENDPOINT_URL`/`AWS_ENDPOINT_URL_S3` overrides when verifying real AWS.

## Cost estimate and Free Tier

As of October 29, 2026, eligibility depends on account creation date, plan, region and remaining
allowances/credits. AWS lists `t3.micro` as Free Tier eligible, but that does not guarantee a zero
bill. Accounts created from July 15, 2025 use the newer six-month/credits offer; older accounts have
different eligibility windows. Check your Billing console before launching.

A rough **paid us-east-1 illustration**, not measured usage: Linux `t3.micro` about $0.0104/hour,
one public IPv4 $0.005/hour, plus 20 GiB gp3 at roughly $0.08/GiB-month (about $1.60/month), S3
storage/requests and any charged transfer. Continuous 730-hour operation would be about $12.84/month
before S3/transfer and credits. A short demo costs much less, but unused eligibility must be checked.
CPU credit mode is Standard to avoid Unlimited surplus CPU charges. No RDS, EKS or NAT Gateway
is provisioned. Prices vary; verify [EC2 pricing](https://aws.amazon.com/ec2/pricing/on-demand/),
[EBS pricing](https://aws.amazon.com/ebs/pricing/), [VPC pricing](https://aws.amazon.com/vpc/pricing/),
[S3 pricing](https://aws.amazon.com/s3/pricing/) and
[Free Tier eligibility](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html).

## Teardown

With the **same AWS profile/account and local state directory**:

```bash
bash deploy/aws/teardown.sh
```

This terminates the instance and waits for termination (root disk deletes), removes the instance
profile and inline role policy/role, deletes its security group, empties the dedicated unversioned
bucket and deletes it. It preserves your existing key pair, VPC and subnet. All demo database,
queue and bucket contents are intentionally destroyed. Do not enable bucket versioning/Object Lock
or add unrelated data to this bucket; the minimal cleanup does not handle those changes.

On failure, state remains and the script can be retried; inspect any AWS permission or network
error. On success it renames `resources.env` to `resources.deleted.env`; remove/archive the local
state before another setup. A crash between creating a resource and writing its ID can require
manual cleanup: check the printed unique `NAME`, AWS console and state. The EC2 client token permits
finding an accepted launch whose instance ID was not yet recorded. Do not share or commit state,
which contains account-specific resource details. Stopping the instance alone does not remove
storage or other billable resources.

## Local checks (October 29, 2026)

Executed against local Linux Docker services: `pytest -q` passed 111 tests; the single skipped test
was the opt-in real AWS bucket test. PostgreSQL, Redis/RQ and MinIO integration tests ran.
`ruff check src tests scripts`, `mypy src/stepwise`, ShellCheck 0.11.0, Bash syntax checking,
the default Compose configuration and the AWS Compose overlay configuration all passed.
Backend and test Docker images built successfully. These checks do not verify EC2 provisioning.

## Executed evidence

_Pending. Fill in only after actually deploying to AWS and running verification. No AWS execution,
timing, resource identifiers or screenshots are claimed here._
