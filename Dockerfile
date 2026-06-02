# Match the production runtime (runtime.txt / render.yaml) exactly so the
# container and Render run the same interpreter and wheels.
FROM python:3.11.10-slim

WORKDIR /app

# System deps occasionally needed when a wheel is unavailable (asyncpg, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
