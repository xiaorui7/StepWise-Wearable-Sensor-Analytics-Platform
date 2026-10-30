#!/usr/bin/env bash
# setup.sh prepends non-secret configuration; runs as root via EC2 user data.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y docker.io docker-compose-v2 git
systemctl enable --now docker
# A micro instance has little RAM for image builds and Matplotlib.
fallocate -l 4G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
git clone --depth 1 --branch "$AWS_GIT_REF" \
  https://github.com/xiaorui7/StepWise-Wearable-Sensor-Analytics-Platform.git /opt/stepwise
cd /opt/stepwise
printf 'AWS_BUCKET=%s\nAWS_REGION=%s\n' "$AWS_BUCKET" "$AWS_REGION" > .env
git rev-parse HEAD > /opt/stepwise-deployed-commit
docker compose -p stepwise-aws -f docker-compose.yml -f deploy/aws/compose.yml \
  up --build -d
