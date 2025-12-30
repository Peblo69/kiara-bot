# Kiara Intelligence - Discord Image Bot

## Overview
A Discord bot powered by Google Gemini for AI image generation. Users can create private "studios" in Discord servers and generate images with various styles and quality settings.

## Project Structure
```
├── bot.py              # Main Discord bot entry point
├── config.py           # Configuration (loads env vars)
├── database.py         # SQLite database operations
├── imagen.py           # Google Gemini image generation
├── rate_limiter.py     # Rate limiting queue
├── grid.py             # Image grid utilities
├── voice/              # Voice AI features (optional)
│   ├── session_manager.py
│   ├── gemini_live.py
│   ├── audio_player.py
│   └── ...
└── models/             # AI model files
    └── hey_love.ppn    # Wake word model
```

## Required Environment Variables
- `DISCORD_TOKEN` - Discord bot token from Discord Developer Portal
- `GOOGLE_API_KEY` - Google API key for Gemini image generation

## Optional Environment Variables
- `DAILY_LIMIT` - Daily generation limit per user (default: 15)
- `RATE_LIMIT_RPM` - Requests per minute limit (default: 10)
- `DEBUG_VOICE` - Set to "1" for verbose voice logging
- `VOICE_PTT_ENABLED` - Enable push-to-talk (default: 0)
- `VOICE_PTT_KEY` - Push-to-talk key (default: "num 3")

## Running the Bot
The bot runs via the "Discord Bot" workflow which executes `python bot.py`.

## Features
- `/panel` - Opens the control panel for image generation
- Private studio channels per user
- Multiple Gemini models (Gemini 3 Pro, 2.5 Flash, 2.5 Preview)
- Reference image support (up to 5)
- Style presets (photorealistic, anime, cyberpunk, etc.)
- Quality settings (1K, 2K, 4K)
- Aspect ratio options
- Daily usage limits

## Database
Uses SQLite (`nano_midjourney.db`) for storing:
- User data and settings
- Daily usage tracking
- Generation history
- Reference images
- Private channel mappings

## Railway Deployment
Discord voice requires UDP connections which Replit blocks. Deploy to Railway for voice features.

### Deployment Files
- `Procfile` - Worker process definition
- `railway.json` - Railway configuration
- `nixpacks.toml` - System dependencies (ffmpeg, libopus)

### Setup Steps
1. Push this repo to GitHub
2. Create new project in Railway from GitHub repo
3. Add environment variables in Railway:
   - `DISCORD_TOKEN`
   - `GOOGLE_API_KEY`
4. Deploy!

### Notes
- Uses py-cord from master branch for latest voice fixes
- Voice auto-activates (no wake word needed)
- Gemini Live API: `gemini-2.0-flash-live-001`

## Recent Changes
- 2024-12-30: Prepared Railway deployment config
- 2024-12-30: Fixed Gemini model to stable version
- 2024-12-30: Initial setup for Replit environment
