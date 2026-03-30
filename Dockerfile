FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY fragebogen_extractor.py .
COPY backend/ backend/
COPY frontend/ frontend/

# Output directory (override via volume)
RUN mkdir -p /data/output

ENV OUTPUT_DIR=/data/output
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
