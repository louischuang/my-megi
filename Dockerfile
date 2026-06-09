FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-tra curl \
  && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /app/
COPY backend /app/backend
COPY cli /app/cli

RUN pip install --no-cache-dir --upgrade pip \
  && pip install --no-cache-dir -e .

EXPOSE 8000

CMD ["uvicorn", "mymegi.main:app", "--host", "0.0.0.0", "--port", "8000"]

