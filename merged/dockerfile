FROM python:3.10-slim

WORKDIR /app

# Prevent Python from buffering logs
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure SQLite folder exists if you're using SQLite
RUN mkdir -p /app/instance

# Railway automatically sets PORT
EXPOSE 8000

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8000} run:app"]
