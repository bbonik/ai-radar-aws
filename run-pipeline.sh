#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# AI Radar AWS - Run Pipeline with Live Progress
#
# Triggers the report pipeline Lambda and streams progress updates in real-time.
# Shows: new announcements found, processing progress (1/N, 2/N...), errors, and final summary.
#
# Usage:
#   ./run-pipeline.sh           # Trigger pipeline + watch progress
#   ./run-pipeline.sh --watch   # Just watch an already-running pipeline
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

FUNCTION_NAME="ai-radar-report-pipeline"
LOG_GROUP="/aws/lambda/${FUNCTION_NAME}"
REGION="us-east-1"
WATCH_ONLY=false

if [[ "${1:-}" == "--watch" ]]; then
    WATCH_ONLY=true
fi

# ─── Trigger the pipeline ─────────────────────────────────────────────────────
if [ "$WATCH_ONLY" = false ]; then
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${BLUE}  AI Radar AWS — Pipeline Runner${NC}"
    echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo -e "${YELLOW}▸ Triggering pipeline Lambda...${NC}"
    aws lambda invoke \
        --function-name "$FUNCTION_NAME" \
        --invocation-type Event \
        --payload '{}' \
        --region "$REGION" \
        /dev/null > /dev/null 2>&1
    echo -e "${GREEN}✓ Pipeline triggered. Waiting for logs...${NC}"
    echo ""
    sleep 3
fi

# ─── Stream logs with progress parsing ────────────────────────────────────────
echo -e "${CYAN}▸ Streaming pipeline progress (Ctrl+C to stop watching)${NC}"
echo ""

PROCESSED=0
TOTAL_RELEVANT=0
ERRORS=0
STARTED=false

aws logs tail "$LOG_GROUP" --since 2m --follow --format short --region "$REGION" 2>&1 | while IFS= read -r line; do
    # Skip empty lines and REPORT/START/END lines
    if [[ -z "$line" ]] || [[ "$line" == *"REPORT RequestId"* ]] || [[ "$line" == *"START RequestId"* ]] || [[ "$line" == *"END RequestId"* ]] || [[ "$line" == *"INIT_START"* ]]; then
        continue
    fi

    # Parse key events from structured JSON logs
    if [[ "$line" == *"Lambda 1 handler invoked"* ]]; then
        echo -e "${GREEN}▸ Pipeline started${NC}"
        STARTED=true
    elif [[ "$line" == *"RSS fetch complete"* ]]; then
        count=$(echo "$line" | grep -o '"total_fetched": [0-9]*' | grep -o '[0-9]*')
        echo -e "  📡 Fetched ${CYAN}${count}${NC} items from RSS feed"
    elif [[ "$line" == *"Deduplication complete"* ]]; then
        new=$(echo "$line" | grep -o '"total_new": [0-9]*' | grep -o '[0-9]*')
        dedup=$(echo "$line" | grep -o '"total_deduplicated": [0-9]*' | grep -o '[0-9]*')
        echo -e "  🔍 New: ${CYAN}${new}${NC} | Already processed: ${dedup}"
    elif [[ "$line" == *"Relevance filtering complete"* ]] && [[ "$line" == *"total_relevant"* ]]; then
        relevant=$(echo "$line" | grep -o '"total_relevant": [0-9]*' | grep -o '[0-9]*')
        TOTAL_RELEVANT=$relevant
        if [ "$relevant" -eq 0 ]; then
            echo -e "  ${YELLOW}⚠ No new AI/ML announcements found${NC}"
        else
            echo -e "  🎯 ${GREEN}${relevant}${NC} relevant AI/ML announcements to process"
            echo ""
        fi
    elif [[ "$line" == *"Announcement processed successfully"* ]]; then
        PROCESSED=$((PROCESSED + 1))
        title=$(echo "$line" | grep -o '"announcement_title": "[^"]*"' | sed 's/"announcement_title": "//;s/"$//')
        stars=$(echo "$line" | grep -o '"importance_level": [0-9]*' | grep -o '[0-9]*')
        star_display=""
        if [ -n "$stars" ]; then
            star_display=" (${stars}★)"
        fi
        echo -e "  ${GREEN}✓${NC} [${PROCESSED}/${TOTAL_RELEVANT}] ${title}${star_display}"
    elif [[ "$line" == *"Announcement processing failed"* ]]; then
        ERRORS=$((ERRORS + 1))
        title=$(echo "$line" | grep -o '"announcement_title": "[^"]*"' | sed 's/"announcement_title": "//;s/"$//')
        error=$(echo "$line" | grep -o '"error_message": "[^"]*"' | sed 's/"error_message": "//;s/"$//' | head -c 80)
        echo -e "  ${RED}✗${NC} [ERROR] ${title}"
        echo -e "         ${RED}${error}${NC}"
    elif [[ "$line" == *"Website builder Lambda invoked"* ]]; then
        echo ""
        echo -e "  ${GREEN}▸ Website rebuild triggered${NC}"
    elif [[ "$line" == *"Pipeline run complete"* ]]; then
        duration=$(echo "$line" | grep -o '"duration_seconds": [0-9.]*' | grep -o '[0-9.]*')
        ok=$(echo "$line" | grep -o '"total_processed_ok": [0-9]*' | grep -o '[0-9]*')
        failed=$(echo "$line" | grep -o '"total_failed": [0-9]*' | grep -o '[0-9]*')
        echo ""
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo -e "${GREEN}  ✓ Pipeline complete${NC} (${duration}s)"
        echo -e "    Processed: ${GREEN}${ok}${NC} | Failed: ${RED}${failed}${NC}"
        echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
        echo ""
        echo -e "  ${CYAN}Website will be live in ~1-2 min (CloudFront invalidation).${NC}"
        echo -e "  Hard-refresh (Cmd+Shift+R) to see new announcements."
        # Exit the log tail
        kill $$ 2>/dev/null || true
        exit 0
    fi
done
