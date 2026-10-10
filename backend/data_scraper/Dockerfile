# Bundles the dashboard with uPlot and its font, so the page needs no CDN.
FROM node:22-alpine AS dashboard
WORKDIR /dashboard
COPY data_scraper/dashboard/package.json data_scraper/dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY data_scraper/dashboard/build.mjs data_scraper/dashboard/index.html data_scraper/dashboard/api.html data_scraper/dashboard/dashboard.css data_scraper/dashboard/dashboard.js ./
RUN npm run build

# Use an official Python runtime as a parent image
FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Set the working directory in the container
WORKDIR /app

# Copy requirements first for better caching
COPY data_scraper/requirements.txt /app/requirements.txt
COPY mongo/requirements.txt /app/mongo/requirements.txt
COPY redis_service/requirements.txt /app/redis_service/requirements.txt
COPY clash_royale_api/requirements.txt /app/clash_royale_api/requirements.txt
COPY api_key_store/requirements.txt /app/api_key_store/requirements.txt
COPY scrape_schedule/requirements.txt /app/scrape_schedule/requirements.txt

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared mongo connection + redis connection + cr api client + schedule + data scraper source
COPY mongo/ /app/mongo/
COPY clash_royale_api/ /app/clash_royale_api/
COPY api_key_store/ /app/api_key_store/
COPY scrape_schedule/ /app/scrape_schedule/
COPY redis_service/ /app/redis_service/
COPY data_scraper/src/ /app/src/
# metrics.py resolves the dashboard relative to src/
COPY --from=dashboard /dashboard/dist /app/dashboard/dist

# Make the package importable
ENV PYTHONPATH=/app

# Run the application
CMD ["sh", "-c", "export CR_API_BOOT_ID=${CR_API_BOOT_ID:-$(cat /proc/sys/kernel/random/uuid)}; exec python src/main.py"]
