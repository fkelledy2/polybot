#!/bin/bash
# logs.sh - Quick alias to tail Heroku logs
# Usage: ./logs.sh [optional filter]
heroku logs --tail -a polybot-trader ${1:+| grep "$1"}
