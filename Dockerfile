# ── Stage 1: build Tailwind CSS ───────────────────────────────────────────────
FROM node:20-alpine AS node-builder

# Copy the full Django project so Tailwind can scan all templates for classes.
WORKDIR /app
COPY orman/ .

WORKDIR /app/theme/static_src
RUN npm ci --prefer-offline
RUN npm run build
# Output lands at /app/theme/static/css/dist/styles.css

# ── Stage 2: Python runtime ───────────────────────────────────────────────────
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBUG=False

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY orman/ .

# Bring in the compiled CSS from the node stage.
COPY --from=node-builder /app/theme/static/css/dist/styles.css \
     theme/static/css/dist/styles.css

# Collect static files.  SECRET_KEY must be non-empty; use a placeholder since
# collectstatic never makes real requests.
RUN SECRET_KEY=collectstatic-placeholder python manage.py collectstatic --noinput

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "orman.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--access-logfile", "-"]
