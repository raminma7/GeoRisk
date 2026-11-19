FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY pyproject.toml ./
COPY model_service ./model_service
RUN pip install .
# trained packages shipped with the service; a newer version in MODEL_DIR (shared volume) takes precedence
COPY models /srv/bundled-models
ENV BUNDLED_MODEL_DIR=/srv/bundled-models
RUN useradd -m svc
USER svc
ENV MODEL_DIR=/models
EXPOSE 8100
CMD ["uvicorn", "model_service.main:app", "--host", "0.0.0.0", "--port", "8100"]
