FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 通过环境变量传入 Webhook URL
ENV DISCORD_WEBHOOK_URL=""

CMD ["python", "main.py"]
