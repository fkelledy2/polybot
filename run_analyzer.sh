#!/bin/bash
# Local wrapper for trading system analyzer
# Run via: bash run_analyzer.sh
# Or via cron: 0 9 * * * cd /path/to/polybot && bash run_analyzer.sh

set -e

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Load environment variables
if [ -f .env ]; then
    export $(cat .env | grep -v '^#' | xargs)
fi

# Run the analyzer
python3 analysis/scheduled_agent.py

# Exit with appropriate code
exit $?
