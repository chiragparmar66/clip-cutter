# ==============================================================================
# Clip Bay Dockerfile
# ==============================================================================

FROM python:3.11-slim

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Deno from official binary release
ENV DENO_VERSION=2.9.5
RUN curl -fsSL https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip -o /tmp/deno.zip \
    && apt-get update \
    && apt-get install -y --no-install-recommends unzip \
    && unzip /tmp/deno.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/deno \
    && rm -f /tmp/deno.zip \
    && apt-get purge -y unzip \
    && rm -rf /var/lib/apt/lists/*

RUN deno --version

# Download bgutil POT provider
RUN git clone --single-branch --branch 2.0.0 \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
    /opt/bgutil-ytdlp-pot-provider

WORKDIR /opt/bgutil-ytdlp-pot-provider/server

# Install provider dependencies
RUN deno install --allow-scripts=npm:canvas --frozen

# Create application user
RUN useradd -m -u 1000 appuser

RUN chown -R appuser:appuser /opt/bgutil-ytdlp-pot-provider

# Application
WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p downloads outputs \
    && chown -R appuser:appuser /app

ENV PYTHONUNBUFFERED=1 \
    PORT=5000 \
    HOST=0.0.0.0

EXPOSE 5000

USER appuser

CMD ["sh", "-c", "cd /opt/bgutil-ytdlp-pot-provider/server && deno run --allow-env --allow-net --allow-ffi=. --allow-read=. src/main.ts --host 127.0.0.1 --port 4416 & exec gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 2 --threads 4 --timeout 600 app:app"]