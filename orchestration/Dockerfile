FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
COPY orchestration/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY orchestration/ .
COPY evaluator/ ./evaluator/

CMD ["python", "-u", "server.py"]
