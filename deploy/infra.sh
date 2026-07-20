#!/bin/bash
# Iteration 3 infra provisioning for the Team247 closed beta.
#
# Idempotent: every resource is looked up by name/tag before being created, so
# re-running this script after a partial failure (or just to check state) is
# safe and will not create duplicates.
#
# Allow-listed resources only, all tagged Project=Team247, region ap-southeast-1:
#   1x EC2 t4g.small, 1x EIP, 1x security group, 1x IAM role+instance-profile,
#   Route53 records in Z05472032AD770JMJ3CAZ, 1x DLM snapshot policy,
#   1x CloudWatch log group (/team247/beta).
set -euo pipefail

REGION="ap-southeast-1"
export AWS_DEFAULT_REGION="$REGION"

NAME="team247-beta"
ROLE_NAME="team247-beta-ec2"
INSTANCE_PROFILE_NAME="team247-beta-ec2"
LOG_GROUP="/team247/beta"
ZONE_ID="Z05472032AD770JMJ3CAZ"
DOMAIN="team247.io"
WWW_DOMAIN="www.team247.io"
DLM_ROLE_NAME="AWSDataLifecycleManagerDefaultRole"
DLM_POLICY_DESC="team247-beta-daily-snapshot"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_DATA_FILE="$SCRIPT_DIR/user-data.sh"

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)

echo "=== [a] default VPC + public subnet ==="
VPC_ID=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text)
if [ -z "$VPC_ID" ] || [ "$VPC_ID" == "None" ]; then
  echo "FATAL: no default VPC found in $REGION" >&2
  exit 1
fi
SUBNET_ID=$(aws ec2 describe-subnets --filters Name=vpc-id,Values="$VPC_ID" Name=default-for-az,Values=true \
  --query 'Subnets[0].SubnetId' --output text)
if [ -z "$SUBNET_ID" ] || [ "$SUBNET_ID" == "None" ]; then
  echo "FATAL: no default public subnet found in $VPC_ID" >&2
  exit 1
fi
echo "VPC=$VPC_ID SUBNET=$SUBNET_ID"

echo "=== [b] security group team247-beta ==="
SG_ID=$(aws ec2 describe-security-groups \
  --filters Name=group-name,Values="$NAME" Name=vpc-id,Values="$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")
if [ -z "$SG_ID" ] || [ "$SG_ID" == "None" ]; then
  SG_ID=$(aws ec2 create-security-group \
    --group-name "$NAME" \
    --description "Team247 closed beta - HTTP/HTTPS ingress" \
    --vpc-id "$VPC_ID" \
    --tag-specifications "ResourceType=security-group,Tags=[{Key=Project,Value=Team247},{Key=Name,Value=$NAME}]" \
    --query 'GroupId' --output text)
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --ip-permissions \
    'IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0}],Ipv6Ranges=[{CidrIpv6=::/0}]' \
    'IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=0.0.0.0/0}],Ipv6Ranges=[{CidrIpv6=::/0}]'
  echo "created SG $SG_ID (default egress = all traffic)"
else
  echo "SG exists: $SG_ID"
fi

echo "=== [c] IAM role + instance profile ==="
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}' \
    --tags Key=Project,Value=Team247 Key=Name,Value="$ROLE_NAME" >/dev/null
  echo "created role $ROLE_NAME"
else
  echo "role exists: $ROLE_NAME"
fi

aws iam attach-role-policy --role-name "$ROLE_NAME" \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam attach-role-policy --role-name "$ROLE_NAME" \
  --policy-arn arn:aws:iam::aws:policy/CloudWatchAgentServerPolicy

INLINE_POLICY=$(cat <<EOF
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SSMParams",
      "Effect": "Allow",
      "Action": ["ssm:GetParameter", "ssm:GetParameters"],
      "Resource": "arn:aws:ssm:${REGION}:*:parameter/team247/prod/*"
    },
    {
      "Sid": "Logs",
      "Effect": "Allow",
      "Action": ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"],
      "Resource": "arn:aws:logs:${REGION}:${ACCOUNT_ID}:log-group:${LOG_GROUP}:*"
    }
  ]
}
EOF
)
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name team247-beta-inline \
  --policy-document "$INLINE_POLICY"
echo "inline policy attached to $ROLE_NAME"

if ! aws iam get-instance-profile --instance-profile-name "$INSTANCE_PROFILE_NAME" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$INSTANCE_PROFILE_NAME" \
    --tags Key=Project,Value=Team247 Key=Name,Value="$INSTANCE_PROFILE_NAME" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$INSTANCE_PROFILE_NAME" --role-name "$ROLE_NAME"
  echo "created instance profile $INSTANCE_PROFILE_NAME, waiting for IAM propagation..."
  sleep 15
else
  echo "instance profile exists: $INSTANCE_PROFILE_NAME"
fi

echo "=== [d] CloudWatch log group $LOG_GROUP ==="
EXISTING_LG=$(aws logs describe-log-groups --log-group-name-prefix "$LOG_GROUP" \
  --query "logGroups[?logGroupName=='${LOG_GROUP}'].logGroupName" --output text)
if [ -z "$EXISTING_LG" ]; then
  aws logs create-log-group --log-group-name "$LOG_GROUP" --tags Project=Team247
  echo "created log group $LOG_GROUP"
else
  echo "log group exists: $LOG_GROUP"
fi
aws logs put-retention-policy --log-group-name "$LOG_GROUP" --retention-in-days 30
echo "retention set to 30d"

echo "=== [e] resolve AL2023 arm64 AMI ==="
AMI_ID=$(aws ssm get-parameter \
  --name /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 \
  --query 'Parameter.Value' --output text)
ROOT_DEVICE=$(aws ec2 describe-images --image-ids "$AMI_ID" --query 'Images[0].RootDeviceName' --output text)
echo "AMI=$AMI_ID root-device=$ROOT_DEVICE"

echo "=== [f] EC2 instance ==="
INSTANCE_ID=$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=${NAME}" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || echo "None")
if [ -z "$INSTANCE_ID" ] || [ "$INSTANCE_ID" == "None" ]; then
  BLOCK_DEV="[{\"DeviceName\":\"${ROOT_DEVICE}\",\"Ebs\":{\"VolumeSize\":20,\"VolumeType\":\"gp3\",\"DeleteOnTermination\":true}}]"
  for attempt in 1 2 3 4 5; do
    if INSTANCE_ID=$(aws ec2 run-instances \
      --image-id "$AMI_ID" \
      --instance-type t4g.small \
      --subnet-id "$SUBNET_ID" \
      --security-group-ids "$SG_ID" \
      --iam-instance-profile "Name=${INSTANCE_PROFILE_NAME}" \
      --user-data "file://${USER_DATA_FILE}" \
      --block-device-mappings "$BLOCK_DEV" \
      --tag-specifications \
        "ResourceType=instance,Tags=[{Key=Project,Value=Team247},{Key=Name,Value=${NAME}}]" \
        "ResourceType=volume,Tags=[{Key=Project,Value=Team247},{Key=Name,Value=${NAME}}]" \
      --query 'Instances[0].InstanceId' --output text 2>/tmp/run-instances.err); then
      break
    else
      echo "run-instances attempt $attempt failed (likely IAM propagation delay), retrying in 10s..." >&2
      cat /tmp/run-instances.err >&2
      sleep 10
      INSTANCE_ID=""
    fi
  done
  if [ -z "$INSTANCE_ID" ]; then
    echo "FATAL: run-instances failed after retries" >&2
    cat /tmp/run-instances.err >&2
    exit 1
  fi
  echo "launched instance $INSTANCE_ID"
else
  echo "instance exists: $INSTANCE_ID"
fi

echo "waiting for instance-running..."
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"
echo "instance running: $INSTANCE_ID"

echo "=== [g] Elastic IP ==="
EIP_ALLOC=$(aws ec2 describe-addresses \
  --filters "Name=tag:Name,Values=${NAME}" "Name=tag:Project,Values=Team247" \
  --query 'Addresses[0].AllocationId' --output text 2>/dev/null || echo "None")
if [ -z "$EIP_ALLOC" ] || [ "$EIP_ALLOC" == "None" ]; then
  EIP_ALLOC=$(aws ec2 allocate-address --domain vpc \
    --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Project,Value=Team247},{Key=Name,Value=${NAME}}]" \
    --query 'AllocationId' --output text)
  echo "allocated EIP $EIP_ALLOC"
else
  echo "EIP exists: $EIP_ALLOC"
fi
EIP_ADDR=$(aws ec2 describe-addresses --allocation-ids "$EIP_ALLOC" --query 'Addresses[0].PublicIp' --output text)

CURRENT_ASSOC_INSTANCE=$(aws ec2 describe-addresses --allocation-ids "$EIP_ALLOC" --query 'Addresses[0].InstanceId' --output text)
if [ "$CURRENT_ASSOC_INSTANCE" != "$INSTANCE_ID" ]; then
  aws ec2 associate-address --instance-id "$INSTANCE_ID" --allocation-id "$EIP_ALLOC" >/dev/null
  echo "associated EIP $EIP_ADDR -> $INSTANCE_ID"
else
  echo "EIP $EIP_ADDR already associated with $INSTANCE_ID"
fi

echo "=== [h] Route53 UPSERT in $ZONE_ID ==="
CHANGE_BATCH=$(cat <<EOF
{
  "Changes": [
    {"Action": "UPSERT", "ResourceRecordSet": {"Name": "${DOMAIN}.", "Type": "A", "TTL": 300, "ResourceRecords": [{"Value": "${EIP_ADDR}"}]}},
    {"Action": "UPSERT", "ResourceRecordSet": {"Name": "${WWW_DOMAIN}.", "Type": "A", "TTL": 300, "ResourceRecords": [{"Value": "${EIP_ADDR}"}]}}
  ]
}
EOF
)
CHANGE_ID=$(aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" \
  --change-batch "$CHANGE_BATCH" --query 'ChangeInfo.Id' --output text)
echo "route53 change submitted: $CHANGE_ID (A ${DOMAIN} -> ${EIP_ADDR}, A ${WWW_DOMAIN} -> ${EIP_ADDR})"

echo "=== [i] DLM daily snapshot policy ==="
if ! aws iam get-role --role-name "$DLM_ROLE_NAME" >/dev/null 2>&1; then
  aws dlm create-default-role >/dev/null
  echo "created DLM default service role $DLM_ROLE_NAME"
  sleep 10
else
  echo "DLM default service role exists: $DLM_ROLE_NAME"
fi
DLM_ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${DLM_ROLE_NAME}"

EXISTING_DLM_POLICY=$(aws dlm get-lifecycle-policies \
  --query "Policies[?Description=='${DLM_POLICY_DESC}'].PolicyId" --output text)
if [ -z "$EXISTING_DLM_POLICY" ]; then
  POLICY_DETAILS=$(cat <<EOF
{
  "ResourceTypes": ["VOLUME"],
  "TargetTags": [{"Key": "Name", "Value": "${NAME}"}],
  "Schedules": [
    {
      "Name": "daily-1200-utc",
      "CreateRule": {"Interval": 24, "IntervalUnit": "HOURS", "Times": ["12:00"]},
      "RetainRule": {"Count": 7},
      "TagsToAdd": [{"Key": "Project", "Value": "Team247"}]
    }
  ]
}
EOF
)
  DLM_POLICY_ID=$(aws dlm create-lifecycle-policy \
    --description "$DLM_POLICY_DESC" \
    --state ENABLED \
    --execution-role-arn "$DLM_ROLE_ARN" \
    --tags Project=Team247 \
    --policy-details "$POLICY_DETAILS" \
    --query 'PolicyId' --output text)
  echo "created DLM policy $DLM_POLICY_ID"
else
  DLM_POLICY_ID="$EXISTING_DLM_POLICY"
  echo "DLM policy exists: $DLM_POLICY_ID"
fi

echo "=== [j] summary ==="
echo "RESOURCE instance_id=$INSTANCE_ID"
echo "RESOURCE eip_allocation_id=$EIP_ALLOC"
echo "RESOURCE eip_address=$EIP_ADDR"
echo "RESOURCE security_group_id=$SG_ID"
echo "RESOURCE iam_role=$ROLE_NAME"
echo "RESOURCE instance_profile=$INSTANCE_PROFILE_NAME"
echo "RESOURCE log_group=$LOG_GROUP"
echo "RESOURCE dlm_policy_id=$DLM_POLICY_ID"
echo "RESOURCE route53_zone=$ZONE_ID ($DOMAIN, $WWW_DOMAIN -> $EIP_ADDR)"
echo "infra.sh complete"
