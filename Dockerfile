FROM python:3.13-slim

# Links Docker image with repository
LABEL org.opencontainers.image.source=https://go.hugobatista.com/gh/telejournal
LABEL security.scan="true"
LABEL maintainer="Hugo Batista <mail@hugobatista.com>"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_ROOT_USER_ACTION=ignore

WORKDIR /app
COPY . /app

RUN pip install --no-cache --upgrade pip \
 && pip install --no-cache /app \
 && addgroup --system --gid 100 app \
 && adduser --system --uid 1000 --ingroup app app \
 && mkdir -p /data \
 && chown -R app:app /data

VOLUME /data

USER app

HEALTHCHECK --interval=300s --timeout=10s --start-period=5s --retries=3 \
    CMD telejournal version || exit 1

ENTRYPOINT ["telejournal","run"]
