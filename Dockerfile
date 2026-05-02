FROM python:3.12-slim

WORKDIR /app

# Install system deps for psycopg binary
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn psycopg_pool

COPY . .

# Run migrations then start gunicorn
CMD ["sh", "-c", "python -m app.db.migrate && gunicorn wsgi:app --bind 0.0.0.0:8000 --workers 4 --timeout 60"]
