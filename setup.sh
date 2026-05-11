#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# AI Radar AWS — One-time environment setup
# ─────────────────────────────────────────────────────────────────────────────
# Run this once after cloning the repo. It checks/installs all prerequisites
# and sets up the Python virtual environment.
#
# Usage:
#   ./setup.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║     AI Radar AWS — Environment Setup     ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""

ERRORS=0

# ─── Check Python ─────────────────────────────────────────────────────────────
echo -e "${YELLOW}▸ Checking Python...${NC}"
if command -v python3.11 &> /dev/null; then
    PYTHON_CMD="python3.11"
    echo -e "${GREEN}  ✓ python3.11 found${NC}"
elif command -v python3 &> /dev/null; then
    PY_VERSION=$(python3 --version 2>&1 | awk '{print $2}')
    PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
    PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
    if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 11 ]; then
        PYTHON_CMD="python3"
        echo -e "${GREEN}  ✓ python3 ($PY_VERSION) found${NC}"
    else
        echo -e "${RED}  ✗ Python 3.11+ required (found $PY_VERSION)${NC}"
        echo -e "    Install: brew install python@3.11  (macOS)"
        ERRORS=$((ERRORS + 1))
        PYTHON_CMD="python3"
    fi
else
    echo -e "${RED}  ✗ Python 3 not found${NC}"
    echo -e "    Install: brew install python@3.11  (macOS)"
    echo -e "             sudo apt install python3.11  (Ubuntu)"
    ERRORS=$((ERRORS + 1))
    PYTHON_CMD="python3"
fi

# ─── Check Node.js ────────────────────────────────────────────────────────────
echo -e "${YELLOW}▸ Checking Node.js...${NC}"
if command -v node &> /dev/null; then
    NODE_VERSION=$(node --version | sed 's/v//')
    NODE_MAJOR=$(echo "$NODE_VERSION" | cut -d. -f1)
    if [ "$NODE_MAJOR" -ge 20 ]; then
        echo -e "${GREEN}  ✓ node $NODE_VERSION found${NC}"
    else
        echo -e "${YELLOW}  ⚠ node $NODE_VERSION found (v20+ recommended, v18 works with warnings)${NC}"
    fi
else
    echo -e "${RED}  ✗ Node.js not found${NC}"
    echo -e "    Install: brew install node  (macOS)"
    echo -e "             https://nodejs.org/en/download"
    ERRORS=$((ERRORS + 1))
fi

# ─── Check AWS CLI ────────────────────────────────────────────────────────────
echo -e "${YELLOW}▸ Checking AWS CLI...${NC}"
if command -v aws &> /dev/null; then
    AWS_VERSION=$(aws --version 2>&1 | awk '{print $1}' | cut -d/ -f2)
    echo -e "${GREEN}  ✓ aws-cli $AWS_VERSION found${NC}"
else
    echo -e "${RED}  ✗ AWS CLI not found${NC}"
    echo -e "    Install: https://aws.amazon.com/cli/"
    ERRORS=$((ERRORS + 1))
fi

# ─── Check AWS credentials ───────────────────────────────────────────────────
echo -e "${YELLOW}▸ Checking AWS credentials...${NC}"
if aws sts get-caller-identity &> /dev/null; then
    ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
    echo -e "${GREEN}  ✓ Authenticated (account: $ACCOUNT)${NC}"
else
    echo -e "${RED}  ✗ AWS credentials not configured${NC}"
    echo -e "    Run: aws configure"
    ERRORS=$((ERRORS + 1))
fi

# ─── Check/Install CDK CLI ───────────────────────────────────────────────────
echo -e "${YELLOW}▸ Checking CDK CLI...${NC}"
if command -v cdk &> /dev/null; then
    CDK_VERSION=$(cdk --version 2>/dev/null | awk '{print $1}')
    echo -e "${GREEN}  ✓ cdk $CDK_VERSION found${NC}"
else
    echo -e "${YELLOW}  Installing CDK CLI...${NC}"
    npm install -g aws-cdk 2>/dev/null
    if command -v cdk &> /dev/null; then
        echo -e "${GREEN}  ✓ CDK CLI installed${NC}"
    else
        echo -e "${RED}  ✗ Failed to install CDK CLI${NC}"
        echo -e "    Run manually: npm install -g aws-cdk"
        ERRORS=$((ERRORS + 1))
    fi
fi

# ─── Stop if prerequisites missing ───────────────────────────────────────────
if [ $ERRORS -gt 0 ]; then
    echo ""
    echo -e "${RED}✗ $ERRORS prerequisite(s) missing. Fix them and re-run ./setup.sh${NC}"
    exit 1
fi

# ─── Create virtual environment ──────────────────────────────────────────────
echo ""
echo -e "${YELLOW}▸ Setting up Python virtual environment...${NC}"
if [ ! -d ".venv" ]; then
    $PYTHON_CMD -m venv .venv
    echo -e "${GREEN}  ✓ Created .venv${NC}"
else
    echo -e "${GREEN}  ✓ .venv already exists${NC}"
fi

# ─── Install dependencies ────────────────────────────────────────────────────
echo -e "${YELLOW}▸ Installing Python dependencies...${NC}"
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt
pip install -q -r requirements-dev.txt
pip install -q aws-cdk-lib constructs
echo -e "${GREEN}  ✓ All dependencies installed${NC}"

# ─── Run tests ───────────────────────────────────────────────────────────────
echo -e "${YELLOW}▸ Running tests to verify setup...${NC}"
TEST_RESULT=$(python -m pytest tests/ -q --tb=line 2>&1 | tail -1)
if echo "$TEST_RESULT" | grep -q "passed"; then
    echo -e "${GREEN}  ✓ $TEST_RESULT${NC}"
else
    echo -e "${YELLOW}  ⚠ $TEST_RESULT${NC}"
fi

# ─── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         ✓ Setup Complete!                ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "Next steps:"
echo -e "  ${BLUE}./deploy.sh${NC}              Deploy the full stack"
echo -e "  ${BLUE}./rebuild-site.sh${NC}        Redeploy after code changes"
echo -e "  ${BLUE}source .venv/bin/activate${NC} Activate the venv for development"
echo ""
echo -e "For more info: ${BLUE}cat README.md${NC}"
