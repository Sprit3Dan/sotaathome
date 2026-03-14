FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir redis langgraph langchain-core fastapi uvicorn pydantic requests kubernetes

COPY . .

CMD ["python", "-u", "main.py"]
