#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# AI Radar AWS - Run Pipeline with Live Progress
#
# Triggers the report pipeline Lambda synchronously and parses the response,
# then tails logs for detailed progress on each announcement.
#
# Usage:
#   ./run-pipeline.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
DIM='\033[2m'
NC='\033[0m'

FUNCTION_NAME="ai-radar-report-pipeline"
LOG_GROUP="/aws/lambda/${FUNCTION_NAME}"
REGION="us-east-1"

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  AI Radar AWS — Pipeline Runner${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# Record start time for log filtering
START_TIME=$(date -u +%s)000

echo -e "${YELLOW}▸ Triggering pipeline Lambda (synchronous — will wait for completion)...${NC}"
echo -e "${DIM}  This may take 1-10 minutes depending on new announcements.${NC}"
echo ""

# Invoke synchronously — blocks until Lambda completes
RESPONSE_FILE=$(mktemp)
aws lambda invoke \
    --function-name "$FUNCTION_NAME" \
    --invocation-type RequestResponse \
    --region "$REGION" \
    --cli-read-timeout 900 \
    "$RESPONSE_FILE" > /dev/null 2>&1

# Parse the Lambda response
RESPONSE=$(cat "$RESPONSE_FILE")
rm -f "$RESPONSE_FILE"

STATUS_CODE=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('statusCode',500))" 2>/dev/null || echo "500")

if [ "$STATUS_CODE" != "200" ]; then
    echo -e "${RED}✗ Pipeline failed with status ${STATUS_CODE}${NC}"
    echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(json.dumps(d.get('body',{}), indent=2))" 2>/dev/null || echo "$RESPONSE"
    exit 1
fi

# Extract summary from response
echo -e "${GREEN}✓ Pipeline completed successfully${NC}"
echo ""

# Parse the summary from logs (more detailed than Lambda response)
echo -e "${CYAN}▸ Fetching run details from logs...${NC}"
echo ""

sleep 2

# Get the latest log stream
LOG_STREAM=$(aws logs describe-log-streams \
    --log-group-name "$LOG_GROUP" \
    --order-by LastEventTime \
    --descending \
    --limit 1 \
    --query 'logStreams[0].logStreamName' \
    --output text \
    --region "$REGION" 2>/dev/null)

if [ -z "$LOG_STREAM" ] || [ "$LOG_STREAM" = "None" ]; then
    echo -e "${YELLOW}  Could not fetch log details.${NC}"
    exit 0
fi

# Fetch all log events from this stream
LOGS=$(aws logs get-log-events \
    --log-group-name "$LOG_GROUP" \
    --log-stream-name "$LOG_STREAM" \
    --start-time "$START_TIME" \
    --query 'events[*].message' \
    --output text \
    --region "$REGION" 2>/dev/null)

# Parse and display key events
echo "$LOGS" | python3 -c "
import sys, json, re

lines = sys.stdin.read().strip().split('\n')
processed = 0
total_relevant = 0

for line in lines:
    line = line.strip()
    if not line or line.startswith('START') or line.startswith('END') or line.startswith('REPORT'):
        continue
    
    # Try to parse as JSON
    try:
        # Handle tab-separated format from aws logs
        if '\t' in line:
            parts = line.split('\t')
            for part in parts:
                part = part.strip()
                if part.startswith('{'):
                    line = part
                    break
        
        if not line.startswith('{'):
            continue
            
        d = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        continue
    
    msg = d.get('message', '')
    
    if msg == 'RSS fetch complete':
        count = d.get('total_fetched', '?')
        print(f'  📡 Fetched {count} items from RSS feed')
    
    elif msg == 'Deduplication complete':
        new = d.get('total_new', '?')
        dedup = d.get('total_deduplicated', '?')
        print(f'  🔍 New: {new} | Already processed: {dedup}')
    
    elif msg == 'Relevance filtering complete' and 'total_relevant' in d:
        total_relevant = d.get('total_relevant', 0)
        if total_relevant == 0:
            print(f'  ⚠️  No new AI/ML announcements found')
        else:
            print(f'  🎯 {total_relevant} relevant AI/ML announcements to process')
            print()
    
    elif msg == 'Announcement processed successfully':
        processed += 1
        title = d.get('announcement_title', 'Unknown')
        stars = d.get('importance_level', '')
        star_str = f' ({stars}★)' if stars else ''
        print(f'  ✓ [{processed}/{total_relevant}] {title}{star_str}')
    
    elif msg == 'Announcement processing failed':
        title = d.get('announcement_title', 'Unknown')
        error = d.get('error_message', 'Unknown error')[:80]
        print(f'  ✗ [ERROR] {title}')
        print(f'         {error}')
    
    elif msg == 'Website builder Lambda invoked successfully':
        print()
        print(f'  🌐 Website rebuild triggered')
    
    elif msg == 'Pipeline run complete':
        summary = d.get('summary', {})
        duration = summary.get('duration_seconds', '?')
        ok = summary.get('total_processed_ok', 0)
        failed = summary.get('total_failed', 0)
        print()
        print('━' * 60)
        print(f'  ✓ Pipeline complete ({duration}s)')
        print(f'    Processed: {ok} | Failed: {failed}')
        print('━' * 60)
        if ok > 0 or total_relevant == 0:
            print()
            print('  Website will be live in ~1-2 min (CloudFront invalidation).')
            print('  Hard-refresh (Cmd+Shift+R) to see new announcements.')
" 2>/dev/null

echo ""
