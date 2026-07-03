FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN pip install --no-cache-dir uv

WORKDIR /app

COPY pyproject.toml ./
COPY app ./app
# The composer reads culinary_anchors.json at runtime, so the image must carry
# seed_data, not just the package.
COPY seed_data ./seed_data
RUN uv pip install --system .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
