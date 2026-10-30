#!/usr/bin/env bash
# Run in Bash with AWS CLI v2. No AWS keys are copied to the instance.
set -euo pipefail
umask 077
export AWS_PAGER=""
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
AWS_REGION=${AWS_REGION:-us-east-1}
AWS_BUCKET=${AWS_BUCKET:-stepwise-demo-$(date +%s)-$RANDOM}
AWS_KEY_PAIR=${AWS_KEY_PAIR:-stepwise-demo}
AWS_INSTANCE_TYPE=${AWS_INSTANCE_TYPE:-t3.micro}
AWS_GIT_REF=${AWS_GIT_REF:-aws-deploy}
STATE=${STEPWISE_AWS_STATE:-$ROOT/deploy/aws/.state}
if [[ -e "$STATE" ]]; then echo "State already exists: $STATE; tear it down first." >&2; exit 1; fi
[[ "$AWS_BUCKET" =~ ^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$ ]]
[[ "$AWS_REGION" =~ ^[a-z0-9-]+$ && "$AWS_GIT_REF" =~ ^[a-zA-Z0-9_./-]+$ ]]
[[ "$AWS_KEY_PAIR" =~ ^[a-zA-Z0-9_.-]+$ ]]
: "${AWS_SSH_CIDR:?Set AWS_SSH_CIDR to your public IPv4 /32 (no public app ports are opened)}"
[[ "$AWS_SSH_CIDR" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}/32$ ]]
aws --version
aws sts get-caller-identity --query Arn --output text >/dev/null
aws ec2 describe-key-pairs --region "$AWS_REGION" --key-names "$AWS_KEY_PAIR" >/dev/null
SUBNET=${AWS_SUBNET_ID:-$(aws ec2 describe-subnets --region "$AWS_REGION" \
  --filters Name=default-for-az,Values=true --query 'Subnets[0].SubnetId' --output text)}
[[ "$SUBNET" != None && -n "$SUBNET" ]] || { echo 'Set AWS_SUBNET_ID to a public subnet.' >&2; exit 1; }
VPC=$(aws ec2 describe-subnets --region "$AWS_REGION" --subnet-ids "$SUBNET" \
  --query 'Subnets[0].VpcId' --output text)
AMI=$(aws ssm get-parameter --region "$AWS_REGION" \
  --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query Parameter.Value --output text)
NAME="stepwise-$(date +%s)-$RANDOM"
mkdir -p "$STATE"
record() { printf '%s=%q\n' "$1" "$2" >> "$STATE/resources.env"; printf '%s=%s\n' "$1" "$2"; }
record AWS_REGION "$AWS_REGION"
record NAME "$NAME"
trap 'echo "Setup stopped; retain state and run teardown.sh with STEPWISE_AWS_STATE=$STATE" >&2' ERR
create=(s3api create-bucket --region "$AWS_REGION" --bucket "$AWS_BUCKET")
if [[ "$AWS_REGION" != us-east-1 ]]; then create+=(--create-bucket-configuration "LocationConstraint=$AWS_REGION"); fi
# CreateBucket in us-east-1 can succeed for an already owned bucket; never adopt it for teardown.
if aws s3api head-bucket --region "$AWS_REGION" --bucket "$AWS_BUCKET" 2> "$STATE/bucket-check.err"; then
  echo 'Bucket already exists; choose a new AWS_BUCKET.' >&2; exit 1
elif ! grep -q '(404)' "$STATE/bucket-check.err"; then
  cat "$STATE/bucket-check.err" >&2; exit 1
fi
aws "${create[@]}" >/dev/null
record AWS_BUCKET "$AWS_BUCKET"
aws s3api put-public-access-block --region "$AWS_REGION" --bucket "$AWS_BUCKET" \
  --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-ownership-controls --region "$AWS_REGION" --bucket "$AWS_BUCKET" \
  --ownership-controls 'Rules=[{ObjectOwnership=BucketOwnerEnforced}]'
aws s3api put-bucket-encryption --region "$AWS_REGION" --bucket "$AWS_BUCKET" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
cat > "$STATE/trust.json" <<'JSON'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}
JSON
cat > "$STATE/policy.json" <<JSON
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":"s3:ListBucket","Resource":"arn:aws:s3:::$AWS_BUCKET"},
  {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject"],"Resource":"arn:aws:s3:::$AWS_BUCKET/*"}
]}
JSON
aws iam create-role --role-name "$NAME" --assume-role-policy-document "file://$STATE/trust.json" >/dev/null
record ROLE "$NAME"
aws iam put-role-policy --role-name "$NAME" --policy-name artifacts --policy-document "file://$STATE/policy.json"
record POLICY artifacts
aws iam create-instance-profile --instance-profile-name "$NAME" >/dev/null
record PROFILE "$NAME"
aws iam add-role-to-instance-profile --instance-profile-name "$NAME" --role-name "$NAME"
SG=$(aws ec2 create-security-group --region "$AWS_REGION" --group-name "$NAME" \
  --description 'StepWise SSH tunnel demo' --vpc-id "$VPC" --query GroupId --output text)
record SG "$SG"
aws ec2 authorize-security-group-ingress --region "$AWS_REGION" --group-id "$SG" \
  --protocol tcp --port 22 --cidr "$AWS_SSH_CIDR" >/dev/null
{
  printf '#!/usr/bin/env bash\n'
  printf 'export AWS_BUCKET=%q AWS_REGION=%q AWS_GIT_REF=%q\n' "$AWS_BUCKET" "$AWS_REGION" "$AWS_GIT_REF"
  cat "$ROOT/deploy/aws/bootstrap.sh"
} > "$STATE/user-data.sh"
# IAM instance profile propagation is eventually consistent. Retrying uses the same EC2 client token.
INSTANCE=""
for attempt in {1..12}; do
  echo "Launching instance (attempt $attempt/12)"
  if INSTANCE=$(aws ec2 run-instances --region "$AWS_REGION" --image-id "$AMI" \
    --instance-type "$AWS_INSTANCE_TYPE" --key-name "$AWS_KEY_PAIR" \
    --iam-instance-profile "Name=$NAME" --client-token "$NAME" \
    --network-interfaces "DeviceIndex=0,SubnetId=$SUBNET,Groups=$SG,AssociatePublicIpAddress=true" \
    --metadata-options HttpEndpoint=enabled,HttpTokens=required,HttpPutResponseHopLimit=2 \
    --credit-specification CpuCredits=standard \
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":20,"VolumeType":"gp3","Encrypted":true,"DeleteOnTermination":true}}]' \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$NAME}]" \
    --user-data "file://$STATE/user-data.sh" --query 'Instances[0].InstanceId' --output text); then break; fi
  sleep 5
done
[[ "$INSTANCE" == i-* ]] || { echo 'Instance launch failed; run teardown.' >&2; exit 1; }
record INSTANCE "$INSTANCE"
aws ec2 wait instance-running --region "$AWS_REGION" --instance-ids "$INSTANCE"
IP=$(aws ec2 describe-instances --region "$AWS_REGION" --instance-ids "$INSTANCE" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
record PUBLIC_IP "$IP"
aws ec2 describe-instances --region "$AWS_REGION" --instance-ids "$INSTANCE" \
  --query 'Reservations[0].Instances[0].BlockDeviceMappings[].Ebs.VolumeId' --output text
echo "State: $STATE. Allow several minutes for cloud-init and image builds."
echo "ssh -i /path/to/$AWS_KEY_PAIR.pem -L 8000:127.0.0.1:8000 -L 5173:127.0.0.1:5173 ubuntu@$IP"
