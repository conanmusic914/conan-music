FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    CONAN_APP_DIR=/app \
    CONAN_MUSIC_ROOT=/app/music \
    CONAN_NO_SCHEDULER=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/music /app/appdata/logs /app/appdata/cache /app/appdata/certs

EXPOSE 8000

CMD ["sh", "-c", "gunicorn wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers 1 --threads 8 --timeout 600"]
