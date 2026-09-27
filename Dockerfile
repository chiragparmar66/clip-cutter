# ==============================================================================
# Clip Bay
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
    unzip \
    && rm -rf /var/lib/apt/lists/*

# ------------------------------------------------------------------------------
# Deno
# ------------------------------------------------------------------------------

ENV DENO_VERSION=2.9.5

RUN curl -fsSL \
    https://github.com/denoland/deno/releases/download/v${DENO_VERSION}/deno-x86_64-unknown-linux-gnu.zip \
    -o /tmp/deno.zip \
    && unzip /tmp/deno.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/deno \
    && rm -f /tmp/deno.zip

RUN deno --version

# ------------------------------------------------------------------------------
# Application user
# ------------------------------------------------------------------------------

RUN useradd -m -u 1000 appuser

# ------------------------------------------------------------------------------
# Python dependencies
# ------------------------------------------------------------------------------

WORKDIR /app

COPY requirements.txt .

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

# ------------------------------------------------------------------------------
# Verify Python packages
# ------------------------------------------------------------------------------

RUN python -c "import yt_dlp; print('yt-dlp:', yt_dlp.version.__version__)"

RUN python -m pip show bgutil-ytdlp-pot-provider

# ------------------------------------------------------------------------------
# Install bgutil-ytdlp-pot-provider server
# ------------------------------------------------------------------------------

WORKDIR /opt

RUN git clone \
    --depth 1 \
    --branch 2.0.0 \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
    bgutil-ytdlp-pot-provider

# ------------------------------------------------------------------------------
# Install bgutil server dependencies
# ------------------------------------------------------------------------------

WORKDIR /opt/bgutil-ytdlp-pot-provider/server

RUN deno install \
    --allow-scripts=npm:canvas \
    --frozen

# ------------------------------------------------------------------------------
# Verify bgutil server files
# ------------------------------------------------------------------------------

RUN test -f /opt/bgutil-ytdlp-pot-provider/server/src/main.ts

# ------------------------------------------------------------------------------
# Application
# ------------------------------------------------------------------------------

WORKDIR /app

COPY . .

# ------------------------------------------------------------------------------
# Runtime directories and permissions
# ------------------------------------------------------------------------------

RUN mkdir -p /app/downloads /app/outputs \
    && chown -R appuser:appuser /app \
    && chown -R appuser:appuser /opt/bgutil-ytdlp-pot-provider

# ------------------------------------------------------------------------------
# Environment
# ------------------------------------------------------------------------------

ENV PYTHONUNBUFFERED=1 \
    PORT=5000 \
    HOST=0.0.0.0

EXPOSE 5000

# ------------------------------------------------------------------------------
# Runtime
# ------------------------------------------------------------------------------

USER appuser

CMD ["sh", "-c", "cd /opt/bgutil-ytdlp-pot-provider/server && deno run --allow-env --allow-net --allow-ffi=. --allow-read=. src/main.ts --host 127.0.0.1 --port 4416 & exec gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 2 --threads 4 --timeout 600 app:app"]