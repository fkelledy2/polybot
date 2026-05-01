#!/bin/bash
# tail.sh - Stream Heroku application logs in real-time
#
# Usage:
#   ./tail.sh              # Stream all logs
#   ./tail.sh error        # Stream only error logs
#   ./tail.sh signal       # Stream only signal-related logs
#   ./tail.sh -h           # Show help
#
# Requires: heroku CLI installed and authenticated
# ─────────────────────────────────────────────────────────────

set -e

# Configuration
APP_NAME="polybot-trader"
FILTER="${1:-}"

# Colors for output
RED='\033[0;31m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Help text
show_help() {
    cat << EOF
${BLUE}tail.sh${NC} - Stream Heroku logs for ${GREEN}${APP_NAME}${NC}

${YELLOW}USAGE:${NC}
  ./tail.sh                           Stream all logs
  ./tail.sh error                     Stream only ERROR logs
  ./tail.sh warning                   Stream only WARNING logs
  ./tail.sh signal                    Stream only signal-related logs
  ./tail.sh backtest                  Stream only backtest logs
  ./tail.sh claude                    Stream only Claude API logs
  ./tail.sh <keyword>                 Stream logs containing <keyword>
  ./tail.sh -h, --help                Show this help message

${YELLOW}EXAMPLES:${NC}
  # Monitor all activity
  ./tail.sh

  # Watch for trading errors
  ./tail.sh error

  # Monitor signal generation
  ./tail.sh "Signal:"

  # Watch for API issues
  ./tail.sh "API error"

${YELLOW}TIPS:${NC}
  • Press Ctrl+C to stop streaming
  • Logs update in real-time
  • Run from the project root directory
  • Make sure 'heroku' CLI is installed: brew install heroku

${YELLOW}FILTERING IN LOGS:${NC}
  Once logs are streaming, you can use grep in another terminal:
    heroku logs --tail -a ${APP_NAME} | grep "YOUR_FILTER"

EOF
}

# Check if heroku CLI is installed
check_heroku_cli() {
    if ! command -v heroku &> /dev/null; then
        echo -e "${RED}Error: heroku CLI not found${NC}"
        echo "Install with: brew install heroku"
        exit 1
    fi
}

# Check if app exists
check_app_exists() {
    if ! heroku apps --json 2>/dev/null | grep -q "\"name\":\"${APP_NAME}\""; then
        echo -e "${RED}Error: App '${APP_NAME}' not found${NC}"
        echo "Make sure you're authenticated: heroku auth:login"
        exit 1
    fi
}

# Main log streaming function
stream_logs() {
    local filter="$1"

    if [ -z "$filter" ]; then
        # Stream all logs
        echo -e "${GREEN}Streaming logs from ${APP_NAME}...${NC}"
        echo -e "${YELLOW}Press Ctrl+C to stop${NC}\n"
        heroku logs --tail -a "${APP_NAME}"
    else
        # Stream filtered logs
        echo -e "${GREEN}Streaming '${filter}' logs from ${APP_NAME}...${NC}"
        echo -e "${YELLOW}Press Ctrl+C to stop${NC}\n"

        case "$filter" in
            error|ERROR)
                heroku logs --tail -a "${APP_NAME}" | grep -i "error"
                ;;
            warning|WARNING)
                heroku logs --tail -a "${APP_NAME}" | grep -i "warning"
                ;;
            signal|SIGNAL)
                heroku logs --tail -a "${APP_NAME}" | grep -i "signal"
                ;;
            backtest|BACKTEST)
                heroku logs --tail -a "${APP_NAME}" | grep -i "backtest"
                ;;
            claude|CLAUDE)
                heroku logs --tail -a "${APP_NAME}" | grep -i "claude\|anthropic\|api"
                ;;
            *)
                # Use the filter as a regex pattern
                heroku logs --tail -a "${APP_NAME}" | grep "$filter"
                ;;
        esac
    fi
}

# Handle signals for clean exit
trap 'echo -e "\n${YELLOW}Log stream stopped${NC}"; exit 0' SIGINT SIGTERM

# Main script
main() {
    case "$FILTER" in
        -h|--help|help)
            show_help
            exit 0
            ;;
        "")
            check_heroku_cli
            check_app_exists
            stream_logs ""
            ;;
        *)
            check_heroku_cli
            check_app_exists
            stream_logs "$FILTER"
            ;;
    esac
}

main
