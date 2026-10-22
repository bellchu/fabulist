FROM python:3.12-slim
# glibc gives every thread its own malloc arena and keeps what a large parse freed: after a dozen big uploads the pod hit its memory limit.
# Two arenas, measured: 20 simultaneous 6 MB Word uploads crashed the pod without it and peaked at 523 MiB with it.
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 MALLOC_ARENA_MAX=2
WORKDIR /srv
# git: BRD repositories are shallow-cloned as a document source.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
RUN useradd --uid 10001 --no-create-home app
USER 10001
ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION
EXPOSE 8000
# Access logs come from the app as JSON (Loki-friendly); uvicorn's own access log is off.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
