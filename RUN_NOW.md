# KIARA INTELLIGENCE - RUN NOW

## Status: READY TO RUN
- Discord Token: SAVED
- Google API Key: SAVED
- All code files: DONE

---

## GIT BASH COMMANDS (copy paste these)

### Step 1: Go to folder
```bash
cd /d/DISCORD/nano-midjourney
```

### Step 2: Create virtual environment
```bash
python -m venv venv
```

### Step 3: Activate it
```bash
source venv/Scripts/activate
```

### Step 4: Install packages
```bash
pip install -r requirements.txt
```

### Step 5: Run the bot
```bash
python bot.py
```

---

## EXPECTED OUTPUT
```
==================================================
  Kiara Intelligence is online!
  Logged in as: Kiara Intelligence#XXXX
  Servers: 2
  Rate limit: 10 RPM
  Daily limit: 15 per user
==================================================
```

---

## TEST IN DISCORD
Once bot is running, go to your Discord server and type:
```
/imagine a beautiful sunset over mountains
```

You'll get:
- 4 images in a grid
- U1 U2 U3 U4 buttons (upscale)
- V1 V2 V3 V4 buttons (variations)
- 🔄 button (re-roll)

---

## IF PYTHON NOT FOUND
Download Python: https://www.python.org/downloads/
Check "Add Python to PATH" during install!

## IF PIP ERRORS
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## IF BOT CRASHES
Check the error message and tell me - I'll fix the code.

---

## FILES IN THIS FOLDER
```
nano-midjourney/
├── bot.py              # Main bot (don't touch)
├── imagen.py           # Google AI (don't touch)
├── grid.py             # Image grid maker
├── rate_limiter.py     # Queue system
├── database.py         # User limits
├── config.py           # Settings
├── requirements.txt    # Dependencies
├── .env                # YOUR SECRETS (Discord + Google keys)
└── RUN_NOW.md          # This file
```

---

## YOUR CREDENTIALS (already saved in .env)
- Discord Token: MTM0MjI1OTcy... (saved)
- Google API Key: AIzaSyBE6kSp... (saved)

---

## QUICK RESTART (if you close terminal)
```bash
cd /d/DISCORD/nano-midjourney
source venv/Scripts/activate
python bot.py
```

---

GO RUN IT! 🚀
