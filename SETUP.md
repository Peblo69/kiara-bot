# Nano Midjourney - Setup Guide

Free Midjourney-style Discord bot powered by Google Imagen 3 ($300 free credits!)

## What You Get

- `/imagine <prompt>` - Generate 4 images in a grid
- **U1-U4** buttons - Upscale individual images
- **V1-V4** buttons - Create variations
- **🔄** button - Re-roll with same prompt
- Daily limits per user (default: 15)
- Rate limiting to prevent API errors

---

## Step 1: Google Cloud Setup (5 min)

### 1.1 Create Account & Get $300 Credit

1. Go to https://console.cloud.google.com
2. Sign in with Google account
3. Click **"Activate"** or **"Start Free Trial"**
4. Add a card (you WON'T be charged - it's just verification)
5. You now have **$300 free credit** for 90 days!

### 1.2 Create Project

1. Click project dropdown (top left) → **New Project**
2. Name: `nano-midjourney`
3. Click **Create**
4. Switch to the new project

### 1.3 Enable Vertex AI

1. Search for "Vertex AI API" in the console
2. Click **Enable**
3. Wait 1-2 minutes

### 1.4 Create Service Account

1. Go to **IAM & Admin** → **Service Accounts**
2. Click **Create Service Account**
3. Name: `discord-bot`
4. Click **Create and Continue**
5. Role: Select **Vertex AI User**
6. Click **Done**

### 1.5 Download JSON Key

1. Find your service account in the list
2. Click the three dots (⋮) → **Manage Keys**
3. **Add Key** → **Create new key** → **JSON**
4. Save the downloaded file as `credentials.json`

---

## Step 2: Discord Bot Setup (5 min)

### 2.1 Create Application

1. Go to https://discord.com/developers/applications
2. Click **New Application**
3. Name: `Nano Midjourney` (or whatever you want)
4. Click **Create**

### 2.2 Get Bot Token

1. Left menu → **Bot**
2. Click **Reset Token** → **Yes**
3. **COPY THE TOKEN** (shown only once!)

### 2.3 Enable Intents

On the Bot page, scroll down and enable:
- ✅ **SERVER MEMBERS INTENT**
- ✅ **MESSAGE CONTENT INTENT**

Click **Save Changes**

### 2.4 Set Permissions & Get Invite Link

1. Left menu → **OAuth2** → **URL Generator**
2. SCOPES: Check ✅ `bot` and ✅ `applications.commands`
3. BOT PERMISSIONS: Check:
   - ✅ Send Messages
   - ✅ Attach Files
   - ✅ Embed Links
   - ✅ Use Slash Commands
4. Copy the generated URL at the bottom
5. Open it in browser and add bot to your server

---

## Step 3: Install & Run (5 min)

### 3.1 Clone/Download the Bot

Put all the bot files in a folder called `nano-midjourney`

### 3.2 Install Python Dependencies

```bash
cd nano-midjourney
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate

pip install -r requirements.txt
```

### 3.3 Configure Environment

1. Copy `.env.example` to `.env`
2. Edit `.env` with your values:

```env
DISCORD_TOKEN=your_bot_token_here
GCP_PROJECT_ID=your_gcp_project_id
GCP_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=credentials.json
DAILY_LIMIT=15
RATE_LIMIT_RPM=10
```

3. Put your `credentials.json` in the same folder

### 3.4 Run the Bot

```bash
python bot.py
```

You should see:
```
==================================================
  Nano Midjourney is online!
  Logged in as: YourBot#1234
  Servers: 1
  Rate limit: 10 RPM
  Daily limit: 15 per user
==================================================
```

---

## Step 4: Test It!

In your Discord server:

1. Type `/imagine a cute robot cat in cyberpunk city`
2. Wait for 4 images to appear
3. Click **U1** to upscale the first image
4. Click **V2** to get variations of the second image
5. Click **🔄** to re-roll

---

## Cost Breakdown

| Action | Cost | With $300 Credit |
|--------|------|-----------------|
| Generate 4 images | $0.08 | 3,750 generations |
| Per user/day (15 gens) | $0.30 | 1,000 users |

---

## Troubleshooting

### "Prompt blocked by safety filter"
Vertex AI blocks some content. Try rephrasing.

### Error 429 (Rate Limit)
Reduce `RATE_LIMIT_RPM` in `.env` to 5 or lower.

### Bot not responding to /imagine
1. Wait 1 hour (Discord caches commands)
2. Or kick and re-add the bot

### "No images generated"
Check your GCP project has Vertex AI enabled and credits remaining.

---

## Deployment (Optional)

For 24/7 hosting, use:
- **Oracle Cloud Free Tier** - Free forever ARM VM
- **Railway.app** - ~$5/month
- **Your own VPS**

See the full guide for deployment instructions.
