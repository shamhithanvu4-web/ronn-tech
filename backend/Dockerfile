# Backend image: FastAPI + uvicorn. Build context is backend/.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN mkdir -p data db evidence fixtures

EXPOSE 8000
# Seed users (from env passwords) and the committed label seed, then serve.
CMD ["sh", "-c", "python scripts/seed_users.py && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
