# Vercel serverless entry for Minato Downloader website.
# Vercel cannot run the Telegram polling bot (main.py) - that needs a
# long-running host. This entry serves only the Flask website.
# Note: on Vercel, the local SQLite files are ephemeral - users, history
# and bot stats reset on redeploy unless a hosted Postgres DATABASE_URL
# is provided and the app is adapted to it (see README).
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from webapp import app
