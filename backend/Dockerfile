FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MPLBACKEND=Agg
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
RUN pip install --no-cache-dir .

RUN addgroup --system stepwise && adduser --system --ingroup stepwise stepwise \
    && mkdir -p /data/artifacts && chown -R stepwise:stepwise /data
USER stepwise

EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn stepwise.api.main:app --host 0.0.0.0 --port 8000"]
