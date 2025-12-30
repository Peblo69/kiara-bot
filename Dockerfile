FROM python:3.11-slim

# Install system dependencies (FFmpeg for voice)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libffi-dev \
    libnacl-dev \
    && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .
# Uninstall discord.py if present (conflicts with py-cord)
RUN pip uninstall -y discord.py discord || true
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Run the bot
CMD ["python", "bot.py"]
