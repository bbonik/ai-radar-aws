#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# AI Radar AWS — One-command deployment script
# ─────────────────────────────────────────────────────────────────────────────
# Usage:
#   ./deploy.sh              # Deploy to default AWS profile/region
#   ./deploy.sh --profile X  # Deploy using a specific AWS profile
#   ./deploy.sh --destroy    # Tear down the stack
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PROFILE_ARG=""
DESTROY=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --profile)
            PROFILE_ARG="--profile $2"
            shift 2
            ;;
        --destroy)
            DESTROY=true
            shift
            ;;
        *)
            echo -e "${RED}Unknown argument: $1${NC}"
            echo "Usage: ./deploy.sh [--profile PROFILE_NAME] [--destroy]"
            exit 1
            ;;
    esac
done

echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║       AI Radar AWS — Deployment          ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""

# ─── Preflight checks ────────────────────────────────────────────────────────
echo -e "${YELLOW}▸ Checking prerequisites...${NC}"

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}✗ python3 not found. Install Python 3.11+${NC}"
    exit 1
fi

if ! command -v cdk &> /dev/null; then
    echo -e "${RED}✗ CDK CLI not found. Run: npm install -g aws-cdk${NC}"
    exit 1
fi

if ! command -v aws &> /dev/null; then
    echo -e "${RED}✗ AWS CLI not found. Install from https://aws.amazon.com/cli/${NC}"
    exit 1
fi

# Verify AWS credentials
if ! aws sts get-caller-identity $PROFILE_ARG &> /dev/null; then
    echo -e "${RED}✗ AWS credentials not configured or expired.${NC}"
    echo "  Run: aws configure $PROFILE_ARG"
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity $PROFILE_ARG --query Account --output text)
REGION=$(python3 -c "from src.config import Config; print(Config().aws_region)")
echo -e "${GREEN}✓ AWS Account: $ACCOUNT_ID | Region: $REGION${NC}"

# ─── Handle destroy ──────────────────────────────────────────────────────────
if [ "$DESTROY" = true ]; then
    echo ""
    echo -e "${RED}⚠  This will DESTROY the AI Radar AWS stack.${NC}"
    echo -e "${YELLOW}   The data bucket (announcement database + generated reports) is RETAINED${NC}"
    echo -e "${YELLOW}   by policy and will survive the destroy. You can delete it in a second step.${NC}"
    read -p "Type 'destroy' to proceed: " confirm
    if [ "$confirm" != "destroy" ]; then
        echo "Cancelled."
        exit 0
    fi

    # Capture the data bucket name before the stack (and its outputs) disappear
    DATA_BUCKET=$(aws cloudformation describe-stack-resources --stack-name AiRadarAwsStack $PROFILE_ARG \
        --query "StackResources[?ResourceType=='AWS::S3::Bucket' && starts_with(LogicalResourceId, 'DataBucket')].PhysicalResourceId" \
        --output text 2>/dev/null || true)

    echo -e "${YELLOW}▸ Destroying stack...${NC}"
    cdk destroy --force $PROFILE_ARG
    echo -e "${GREEN}✓ Stack destroyed.${NC}"

    if [ -n "$DATA_BUCKET" ] && [ "$DATA_BUCKET" != "None" ]; then
        OBJECT_COUNT=$(aws s3api list-objects-v2 --bucket "$DATA_BUCKET" $PROFILE_ARG \
            --query 'KeyCount' --output text 2>/dev/null || echo "?")
        echo ""
        echo -e "${YELLOW}The data bucket was retained: ${DATA_BUCKET} (${OBJECT_COUNT} objects).${NC}"
        echo -e "It contains the announcement database and all generated reports."
        echo -e "${RED}Deleting it is IRREVERSIBLE${NC} — consider 'python scripts/backup.py' first."
        read -p "Also delete the data bucket and ALL its contents? Type 'delete-data' to confirm: " confirm2
        if [ "$confirm2" = "delete-data" ]; then
            echo -e "${YELLOW}▸ Emptying bucket (all versions) and deleting...${NC}"
            # Versioned bucket: must delete all object versions before the bucket.
            # boto3 handles pagination and batching; honour --profile via AWS_PROFILE.
            if [ -n "$PROFILE_ARG" ]; then
                export AWS_PROFILE="${PROFILE_ARG#--profile }"
            fi
            python3 - "$DATA_BUCKET" <<'PYEOF'
import sys
import boto3

bucket = boto3.resource("s3").Bucket(sys.argv[1])
bucket.object_versions.delete()
bucket.delete()
print(f"Deleted bucket {sys.argv[1]}")
PYEOF
            echo -e "${GREEN}✓ Data bucket deleted.${NC}"
        else
            echo -e "Data bucket kept: ${DATA_BUCKET}"
            echo -e "${YELLOW}Note:${NC} redeploying into this account may conflict with the orphaned"
            echo -e "bucket. Delete it later with: aws s3 rb s3://${DATA_BUCKET} --force"
        fi
    fi
    exit 0
fi

# ─── Install dependencies ────────────────────────────────────────────────────
echo -e "${YELLOW}▸ Installing dependencies...${NC}"

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi

source .venv/bin/activate
pip install -q -r requirements.txt
pip install -q -r requirements-dev.txt
pip install -q aws-cdk-lib constructs

# ─── Run tests ───────────────────────────────────────────────────────────────
# Full output goes to a log; on failure the whole log is shown (the old
# `| tail -5` cut off the actual assertion on most failures).
echo -e "${YELLOW}▸ Running tests...${NC}"
TEST_LOG=$(mktemp)
if python -m pytest tests/ -q --tb=short > "$TEST_LOG" 2>&1; then
    tail -2 "$TEST_LOG"
else
    cat "$TEST_LOG"
    rm -f "$TEST_LOG"
    echo -e "${RED}✗ Tests failed — aborting deploy.${NC}"
    exit 1
fi
rm -f "$TEST_LOG"

# ─── Bootstrap CDK (if needed) ───────────────────────────────────────────────
echo -e "${YELLOW}▸ Bootstrapping CDK (if needed)...${NC}"
cdk bootstrap aws://$ACCOUNT_ID/$REGION $PROFILE_ARG 2>&1 | grep -E "(✅|already)" || true

# ─── Deploy ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}▸ Deploying AI Radar AWS stack...${NC}"
cdk deploy --require-approval never $PROFILE_ARG

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         ✓ Deployment Complete!           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""

# Warn if alarms have no subscriber (topic exists, nobody listening)
if ! grep -q '"alert_email"' cdk.context.json 2>/dev/null; then
    echo -e "${YELLOW}⚠  No alert_email in cdk.context.json — CloudWatch alarms publish to the${NC}"
    echo -e "${YELLOW}   'ai-radar-alerts' SNS topic, but nobody is subscribed. Add it and redeploy,${NC}"
    echo -e "${YELLOW}   or subscribe manually in the SNS console.${NC}"
    echo ""
fi

# Show the website URL from stack outputs
WEBSITE_URL=$(aws cloudformation describe-stacks --stack-name AiRadarAwsStack $PROFILE_ARG --query 'Stacks[0].Outputs[?OutputKey==`WebsiteUrl`].OutputValue' --output text 2>/dev/null)
if [ -n "$WEBSITE_URL" ]; then
    echo -e "${GREEN}🌐 Website URL: $WEBSITE_URL${NC}"
    echo ""
fi

echo -e "The pipeline will run daily at the configured schedule."
echo -e "To trigger it manually (runs async, check CloudWatch Logs for results):"
echo -e "  aws lambda invoke --function-name ai-radar-report-pipeline --invocation-type Event $PROFILE_ARG /dev/null"
