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
    echo -e "${RED}⚠  This will DESTROY the AI Radar AWS stack and all its resources.${NC}"
    read -p "Are you sure? (yes/no): " confirm
    if [ "$confirm" = "yes" ]; then
        echo -e "${YELLOW}▸ Destroying stack...${NC}"
        cdk destroy --force $PROFILE_ARG
        echo -e "${GREEN}✓ Stack destroyed.${NC}"
    else
        echo "Cancelled."
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
echo -e "${YELLOW}▸ Running tests...${NC}"
python -m pytest tests/ -q --tb=short 2>&1 | tail -5

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
echo -e "The pipeline will run daily at the configured schedule."
echo -e "To trigger it manually:"
echo -e "  aws lambda invoke --function-name ai-radar-report-pipeline $PROFILE_ARG /dev/null"
echo ""
echo -e "To find your CloudFront URL:"
echo -e "  aws cloudfront list-distributions $PROFILE_ARG --query 'DistributionList.Items[?Comment==\`\`].DomainName' --output text"
