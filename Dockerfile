# Use Python 3.11 slim image
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY pm_bot.py .

# Run the bot
CMD ["python3", "pm_bot.py"]
