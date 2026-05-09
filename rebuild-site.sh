#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# AI Radar AWS — Rebuild & redeploy website
# ─────────────────────────────────────────────────────────────────────────────
# Usage:
#   ./rebuild-site.sh              # Deploy code + rebuild website
#   ./rebuild-site.sh --skip-cdk   # Just rebuild website (no CDK deploy)
#   ./rebuild-site.sh --pipeline   # Run full pipeline (fetch news + rebuild)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

SKIP_CDK=false
RUN_PIPELINE=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --skip-cdk) SKIP_CDK=true; shift ;;
        --pipeline) RUN_PIPELINE=true; shift ;;
        *) echo "Unknown: $1. Usage: ./rebuild-site.sh [--skip-cdk] [--pipeline]"; exit 1 ;;
    esac
done

echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     AI Radar AWS — Rebuild Website       ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""

# ─── Step 1: Deploy updated Lambda code (unless skipped) ─────────────────────
if [ "$SKIP_CDK" = false ]; then
    echo -e "${YELLOW}▸ Deploying updated code...${NC}"
    cdk deploy --require-approval never 2>&1 | grep -E "(✅|✨|❌)" || true
    echo -e "${GREEN}✓ Code deployed${NC}"
    echo ""
fi

# ─── Step 2: Run pipeline or just rebuild website ────────────────────────────
if [ "$RUN_PIPELINE" = true ]; then
    echo -e "${YELLOW}▸ Triggering full pipeline (fetch news + generate reports + rebuild site)...${NC}"
    aws lambda invoke --function-name ai-radar-report-pipeline --invocation-type Event /dev/null --region us-east-1 > /dev/null 2>&1
    echo -e "${GREEN}✓ Pipeline triggered (runs async, ~5-10 min)${NC}"
    echo -e "  It will automatically rebuild the website when done."
else
    echo -e "${YELLOW}▸ Rebuilding website from existing data...${NC}"
    echo '{"run_id":"manual-rebuild","source":"manual"}' > /tmp/_rebuild_payload.json
    aws lambda invoke --function-name ai-radar-website-builder --invocation-type Event --payload fileb:///tmp/_rebuild_payload.json /dev/null --region us-east-1 > /dev/null 2>&1
    rm -f /tmp/_rebuild_payload.json
    echo -e "${GREEN}✓ Website builder triggered (takes ~5 seconds)${NC}"
fi

echo ""

# ─── Show website URL ────────────────────────────────────────────────────────
WEBSITE_URL=$(aws cloudformation describe-stacks --stack-name AiRadarAwsStack --query 'Stacks[0].Outputs[?OutputKey==`WebsiteUrl`].OutputValue' --output text --region us-east-1 2>/dev/null)
if [ -n "$WEBSITE_URL" ] && [ "$WEBSITE_URL" != "None" ]; then
    echo -e "${GREEN}🌐 Website: $WEBSITE_URL${NC}"
else
    echo -e "  (CloudFront URL will appear after first deploy with outputs)"
fi
echo -e "  Hard-refresh (Cmd+Shift+R) to bypass browser cache."
