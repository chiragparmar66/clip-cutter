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
# Create application user
# ------------------------------------------------------------------------------

RUN useradd -m -u 1000 appuser

# ------------------------------------------------------------------------------
# Install Python dependencies
# ------------------------------------------------------------------------------

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# ------------------------------------------------------------------------------
# Install bgutil server
#
# Plugin is installed through pip above.
# Server is kept at the exact same version.
# ------------------------------------------------------------------------------

WORKDIR /opt

RUN git clone \
    --single-branch \
    --branch 2.0.0 \
    https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git \
    bgutil-ytdlp-pot-provider

WORKDIR /opt/bgutil-ytdlp-pot-provider/server

RUN deno install \
    --allow-scripts=npm:canvas \
    --frozen

# ------------------------------------------------------------------------------
# Verify versions
# ------------------------------------------------------------------------------

RUN python -c "import yt_dlp; print('yt-dlp:', yt_dlp.version.__version__)"

RUN python -c "import bgutil_ytdlp_pot_provider; print('bgutil Python plugin: OK')"

# ------------------------------------------------------------------------------
# Application
# ------------------------------------------------------------------------------

WORKDIR /app

COPY . .

# ------------------------------------------------------------------------------
# Runtime directories
# ------------------------------------------------------------------------------

RUN mkdir -p downloads outputs \
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
# Run
# ------------------------------------------------------------------------------

USER appuser

CMD ["sh", "-c", "cd /opt/bgutil-ytdlp-pot-provider/server && deno run --allow-env --allow-net --allow-ffi=. --allow-read=. src/main.ts --host 127.0.0.1 --port 4416 & exec gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 2 --threads 4 --timeout 600 app:app"]