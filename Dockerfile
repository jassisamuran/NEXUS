FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    git \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install CPU-only torch first (much smaller, ~200MB vs 530MB)
RUN pip install --no-cache-dir \
    torch==2.1.0+cpu \
    --index-url https://download.pytorch.org/whl/cpu

# Install everything else
RUN pip install --no-cache-dir \
    --default-timeout=1000 \
    --retries=5 \
    -r requirements.txt

COPY . .

ENV PYTHONPATH=/app