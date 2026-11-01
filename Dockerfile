# syntax=docker/dockerfile:1
FROM python:3.13.18-alpine3.23@sha256:e4aeaca9081549f19bd5bc8605ed5ab257ba34f97e0471f8e09161f45b4b08b8 AS base

# aiohttp uses OpenSSL's system roots, independently of certifi.
RUN apk upgrade --no-cache && \
    apk add --no-cache 'ca-certificates>=20261020-r0' 'ca-certificates-bundle>=20261020-r0' \
        'musl>=1.2.5-r24' 'musl-utils>=1.2.5-r24' && \
    update-ca-certificates

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

# COPY cannot remove the base image's pip; remove its roots and ensurepip's recovery wheel.
RUN python3 -m pip uninstall -y pip && \
    rm -rf /usr/local/lib/python3.13/ensurepip

WORKDIR /app

COPY --from=compiler /app .

ENTRYPOINT ["python3", "-u", "main.pyc"]
