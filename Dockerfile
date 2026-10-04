FROM python:3.11-slim

# Install system deps: ffmpeg for audio handling, curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r appuser && useradd -r -g appuser -u 1000 appuser

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY .env.example .env.example

WORKDIR /app/backend

# Create runtime dirs and chown to appuser
RUN mkdir -p /app/backend/data /app/backend/uploads /app/backend/.cache && \
    chown -R appuser:appuser /app

USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENV DATABASE_URL=sqlite:////app/backend/data/pw_qa.db
ENV CACHE_DIR=/app/backend/.cache

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

