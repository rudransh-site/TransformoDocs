FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-hin \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV TESSERACT_CMD=/usr/bin/tesseract

RUN python manage.py collectstatic --noinput

EXPOSE 10000

CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py seed_initial_data && python manage.py bootstrap_admin && gunicorn transformodocs.wsgi:application --bind 0.0.0.0:${PORT:-10000}"]

