#!/bin/bash
# Local host for telegram-downloader with a local SQLite DB.
# BOT_TOKEN must be set in the environment (never hard-code it here).
cd "$(dirname "$0")"
export DATABASE_URL="sqlite:////home/hatch/workspace/host-telegram-downloader/data/bot.db"
if [ -z "$BOT_TOKEN" ]; then
  echo "BOT_TOKEN is not set. Set it in the environment and run again."
  exit 1
fi
exec .venv/bin/python main.py
