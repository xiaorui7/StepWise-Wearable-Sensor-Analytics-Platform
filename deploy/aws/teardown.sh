#!/usr/bin/env bash
# Deletes only the resources recorded by setup.sh. Does not delete a supplied key pair or VPC.
set -euo pipefail
export AWS_PAGER=""
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
STATE=${STEPWISE_AWS_STATE:-$ROOT/deploy/aws/.state}
[[ -f "$STATE/resources.env" ]] || { echo "No deployment state: $STATE" >&2; exit 1; }
# This file is generated locally by setup.sh; never source an untrusted state directory.
# shellcheck source=/dev/null
source "$STATE/resources.env"
# Ignore only explicit missing-resource responses; access/network errors must keep the state.
missing_ok() {
  if "$@" 2> "$STATE/teardown.err"; then return; fi
  if grep -Eq 'NoSuchEntity|NoSuchBucket|InvalidGroup.NotFound' "$STATE/teardown.err"; then return; fi
  cat "$STATE/teardown.err" >&2
  return 1
}
# Recover a launch interrupted after AWS accepted it but before setup recorded its ID.
if [[ -z "${INSTANCE:-}" ]]; then
  INSTANCE=$(aws ec2 describe-instances --region "$AWS_REGION" --filters "Name=client-token,Values=$NAME" \
    --query 'Reservations[].Instances[].InstanceId' --output text)
fi
if [[ -n "${INSTANCE:-}" ]]; then
  aws ec2 terminate-instances --region "$AWS_REGION" --instance-ids "$INSTANCE" >/dev/null
  aws ec2 wait instance-terminated --region "$AWS_REGION" --instance-ids "$INSTANCE"
  echo "Terminated $INSTANCE (root volume deletes on termination)"
fi
if [[ -n "${PROFILE:-}" ]]; then
  missing_ok aws iam remove-role-from-instance-profile --instance-profile-name "$PROFILE" --role-name "$ROLE"
  missing_ok aws iam delete-instance-profile --instance-profile-name "$PROFILE"
fi
if [[ -n "${ROLE:-}" ]]; then
  missing_ok aws iam delete-role-policy --role-name "$ROLE" --policy-name artifacts
  missing_ok aws iam delete-role --role-name "$ROLE"
  echo "Deleted role $ROLE and inline policy"
fi
if [[ -n "${SG:-}" ]]; then
  # ENI cleanup can lag instance termination; keep state on any permanent failure.
  for attempt in {1..12}; do
    if missing_ok aws ec2 delete-security-group --region "$AWS_REGION" --group-id "$SG"; then break; fi
    if [[ "$attempt" == 12 ]]; then exit 1; fi
    sleep 5
  done
  echo "Deleted security group $SG"
fi
if [[ -n "${AWS_BUCKET:-}" ]]; then
  # Dedicated unversioned demo bucket: this intentionally deletes all its objects.
  missing_ok aws s3 rm "s3://$AWS_BUCKET" --recursive --region "$AWS_REGION"
  missing_ok aws s3api delete-bucket --bucket "$AWS_BUCKET" --region "$AWS_REGION"
  echo "Deleted bucket $AWS_BUCKET"
fi
mv "$STATE/resources.env" "$STATE/resources.deleted.env"
echo "Teardown complete. Remove the local state directory before another setup."
