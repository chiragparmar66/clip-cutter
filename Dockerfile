# ==============================================================================
# Clip Bay Dockerfile
# ==============================================================================

FROM python:3.11-slim

# ------------------------------------------------------------------------------
# System dependencies
# ------------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------------------------
# Install Deno 2.x
# ------------------------------------------------------------------------------
ENV DENO_INSTALL=/opt/deno

RUN curl -fsSL https://deno.land/install.sh | sh

ENV PATH="/opt/deno/bin:${PATH}"

RUN deno --version

# ------------------------------------------------------------------------------
# Download bgutil PO-token provider
# ------------------------------------------------------------------------------
RUN git clone --single-branch --branch 2.0.0 \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
    /opt/bgutil-ytdlp-pot-provider

# ------------------------------------------------------------------------------
# Install provider dependencies
# ------------------------------------------------------------------------------
WORKDIR /opt/bgutil-ytdlp-pot-provider/server

RUN deno install --allow-scripts=npm:canvas --frozen

# ------------------------------------------------------------------------------
# Create application user
# ------------------------------------------------------------------------------
RUN useradd -m -u 1000 appuser

# Provider must be readable/executable by appuser
RUN chown -R appuser:appuser /opt/bgutil-ytdlp-pot-provider \
    /opt/deno

# ------------------------------------------------------------------------------
# Application
# ------------------------------------------------------------------------------
WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p downloads outputs \
    && chown -R appuser:appuser /app

# ------------------------------------------------------------------------------
# Runtime environment
# ------------------------------------------------------------------------------
ENV PYTHONUNBUFFERED=1 \
    PORT=5000 \
    HOST=0.0.0.0

EXPOSE 5000

<<<<<<< Updated upstream
# Run with Gunicorn using threads for concurrent status polling
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 1 --threads 4 --timeout 600 app:app"]
=======
USER appuser

# ------------------------------------------------------------------------------
# Start:
# 1. bgutil PO-token provider on localhost:4416
# 2. Flask/Gunicorn application
# ------------------------------------------------------------------------------
CMD ["sh", "-c", "cd /opt/bgutil-ytdlp-pot-provider/server/node_modules && deno run --allow-env --allow-net --allow-ffi=. --allow-read=. ../src/main.ts --host 127.0.0.1 --port 4416 & exec gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 2 --threads 4 --timeout 600 app:app"]
>>>>>>> Stashed changes
