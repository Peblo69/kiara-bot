import os
from dotenv import load_dotenv

load_dotenv()

# Discord
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# Google API Key (simple!)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Limits
DAILY_LIMIT = int(os.getenv("DAILY_LIMIT", "15"))
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "10"))

# Bot branding
BOT_NAME = "Kiara Intelligence"
BOT_COLOR = 0x5865F2  # Discord blurple
