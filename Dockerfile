# Dragon Mail (T-0049). Built by GitHub Actions and published to ghcr.io.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

# Unprivileged user; drawings go to /data/images (mount a volume there).
RUN useradd --system --uid 10001 --home /srv dragonmail && mkdir -p /data/images && chown -R dragonmail /data
USER dragonmail
ENV IMAGES_DIR=/data/images
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)"
# --proxy-headers off: the real client IP comes from CF-Connecting-IP (app/clientip.py), never X-Forwarded-For.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
