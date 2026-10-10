# Use an official Python runtime as a parent image
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Set the working directory in the container
WORKDIR /app

# Copy requirements first for better caching
COPY backend/app/requirements.txt /app/requirements.txt
COPY backend/mongo/requirements.txt /app/mongo/requirements.txt
COPY backend/redis_service/requirements.txt /app/redis_service/requirements.txt
COPY backend/clash_royale_api/requirements.txt /app/clash_royale_api/requirements.txt
COPY backend/api_key_store/requirements.txt /app/api_key_store/requirements.txt
COPY backend/scrape_schedule/requirements.txt /app/scrape_schedule/requirements.txt

# Install any needed packages specified in requirements.txt
RUN apt-get update && apt-get install -y --no-install-recommends libcairo2 \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared mongo connection + redis connection + cr api client + api source 
COPY backend/mongo/ /app/mongo/
COPY backend/clash_royale_api/ /app/clash_royale_api/
COPY backend/api_key_store/ /app/api_key_store/
COPY backend/scrape_schedule/ /app/scrape_schedule/
COPY backend/redis_service/ /app/redis_service/
COPY backend/app/src/ /app/src/

# Copy shared resources for Word Guess
COPY shared_resources/ /app/shared_resources/

# Make the package importable
ENV PYTHONPATH=/app

# Expose port
EXPOSE 8000

# The API is only reachable on the Compose network; nginx replaces forwarded IP headers.
# Trust that internal boundary so Uvicorn exposes the resolved IP as request.client.host.
CMD ["sh", "-c", "export CR_API_BOOT_ID=${CR_API_BOOT_ID:-$(cat /proc/sys/kernel/random/uuid)}; exec uvicorn main:app --app-dir /app/src --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*'"]

