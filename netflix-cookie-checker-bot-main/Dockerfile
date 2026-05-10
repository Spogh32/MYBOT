FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY scripts/netflix-bot/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts/netflix-bot/ ./

ENV PYTHONUNBUFFERED=1

CMD ["python", "bot.py"]
