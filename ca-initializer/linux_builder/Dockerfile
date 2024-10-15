FROM node:16-buster AS builder

RUN mkdir /app
WORKDIR /app

COPY . .
RUN npm ci

RUN npm run build:linux:x64
