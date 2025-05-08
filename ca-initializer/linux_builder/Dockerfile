FROM node:16-bullseye-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir /app
WORKDIR /app

COPY . .
RUN npm ci

RUN npm run build:linux:x64
