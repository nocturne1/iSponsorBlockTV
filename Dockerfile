# syntax=docker/dockerfile:1
FROM python:3.13-alpine3.22 AS base

RUN apk upgrade --no-cache

FROM base AS compiler

WORKDIR /app

COPY src .

RUN python3 -m compileall -b -f . && \
    find . -name "*.py" -type f -delete

FROM base AS dep_installer

COPY requirements.txt .

RUN apk add --no-cache gcc musl-dev && \
    pip install --upgrade pip wheel && \
    pip install -r requirements.txt && \
    pip uninstall -y pip wheel && \
    apk del gcc musl-dev && \
    python3 -m compileall -b -f /usr/local/lib/python3.13/site-packages && \
    find /usr/local/lib/python3.13/site-packages -name "*.py" -type f -delete && \
    find /usr/local/lib/python3.13/ -name "__pycache__" -type d -exec rm -rf {} +

FROM base

ENV PIP_NO_CACHE_DIR=off iSPBTV_docker=True iSPBTV_data_dir=data TERM=xterm-256color COLORTERM=truecolor

COPY requirements.txt .

COPY --from=dep_installer /usr/local /usr/local

WORKDIR /app

COPY --from=compiler /app .

RUN python3 -m pip uninstall -y pip wheel || true && \
    find /usr/local/lib/python3.13/ -name "__pycache__" -type d -exec rm -rf {} +

ENTRYPOINT ["python3", "-u", "main.pyc"]
