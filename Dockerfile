FROM python:3.11-slim

# Install system dependencies (FFmpeg for voice)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libffi-dev \
    libnacl-dev \
    && rm -rf /var/lib/apt/lists/*

# Set work directory
WORKDIR /app

# CACHE BUST v2 - force clean install
COPY requirements.txt .
# Remove ANY discord package first, then install py-cord
RUN pip uninstall -y discord.py discord py-cord pycord 2>/dev/null || true
RUN pip install --no-cache-dir py-cord[voice]==2.6.1
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Run the bot
CMD ["python", "bot.py"]
