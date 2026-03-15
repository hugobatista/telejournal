FROM python:3.13-slim

# Links Docker image with repository
LABEL org.opencontainers.image.source=https://go.hugobatista.com/gh/telejournal
LABEL security.scan="true"
LABEL maintainer="Hugo Batista <mail@hugobatista.com>"

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_ROOT_USER_ACTION=ignore

WORKDIR /telejournal
COPY pyproject.toml README.md /telejournal/
COPY src /telejournal/src
COPY config.example.yaml /telejournal/config/config.example.yaml


RUN pip install --no-cache --upgrade pip \
 && pip install --no-cache /telejournal \
 && addgroup --system --gid 1000 telejournal \
 && adduser --system --uid 1000 --ingroup telejournal telejournal \
 && mkdir -p /telejournal/data \
 && mkdir -p /telejournal/config \
 && chown -R telejournal:telejournal /telejournal/data \
 && chown -R telejournal:telejournal /telejournal/config

VOLUME /telejournal/data
VOLUME /telejournal/config

USER telejournal

HEALTHCHECK --interval=300s --timeout=10s --start-period=5s --retries=3 \
    CMD telejournal version || exit 1

ENTRYPOINT ["telejournal","run","/telejournal/config/config.example.yaml"]
