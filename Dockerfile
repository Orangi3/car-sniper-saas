# FlipFirst — one container: migrations, worker, scheduler and web.
# Used by render.yaml (and any host that runs a Dockerfile). The VPS path in
# DEPLOY.md / deploy/ is unchanged. Start-up order: deploy/paas/start.sh.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Files left out of the image are listed in .dockerignore (docs, tests,
# Mac launch scripts, local databases, secrets).
COPY . .

# Run as an unprivileged user; it owns /app only for the caches the app
# writes next to the code (comps/vin caches, request log).
RUN useradd --system --uid 10001 --home /app flipfirst \
 && chown -R flipfirst /app \
 && chmod +x deploy/paas/start.sh
USER flipfirst

ENV PORT=8765
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT','8765'), timeout=4)"

CMD ["deploy/paas/start.sh"]
