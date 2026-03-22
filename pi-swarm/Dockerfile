FROM python:3.11-slim-bookworm

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        cmake build-essential git && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080 8000

# Default: orchestrator. Override CMD in docker-compose for worker.
CMD ["python3", "-m", "uvicorn", "src.orchestrator.app:app", \
     "--host", "0.0.0.0", "--port", "8080", "--log-level", "info"]
