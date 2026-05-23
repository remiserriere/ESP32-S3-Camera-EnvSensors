FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY gauge_service ./gauge_service

EXPOSE 8081
VOLUME ["/data"]

CMD ["python", "-m", "gauge_service.app"]
