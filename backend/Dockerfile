# backend/Dockerfile
FROM python:3.11-slim

# Install system dependencies: ffmpeg for audio handling, curl for container health check
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user and group
RUN groupadd -r appuser && useradd -r -g appuser -u 1000 appuser

WORKDIR /app

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend application files
COPY . .

# Create runtime directories and grant ownership to non-root appuser
RUN mkdir -p /app/data /app/uploads /app/.cache && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Healthcheck hitting /health
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

ENV DATABASE_URL=sqlite:////app/data/pw_qa.db
ENV CACHE_DIR=/app/.cache
ENV LOG_LEVEL=INFO

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
