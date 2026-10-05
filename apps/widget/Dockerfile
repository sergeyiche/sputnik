# Multi-stage: build Vue widget and admin → serve demo page, widget and admin via nginx
FROM node:22-alpine AS build

WORKDIR /build
COPY apps/widget/package.json apps/widget/package-lock.json ./
RUN npm ci
COPY apps/widget/ ./
RUN npm run build

FROM node:22-alpine AS build-admin

WORKDIR /build
COPY apps/admin/package.json apps/admin/package-lock.json ./
RUN npm ci
COPY apps/admin/ ./
RUN npm run build

FROM nginx:1.27-alpine

COPY apps/widget/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist/ /usr/share/nginx/html/widget/
COPY --from=build-admin /build/dist/ /usr/share/nginx/html/admin/
COPY sample-site/ /usr/share/nginx/html/

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
