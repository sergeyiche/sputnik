# Multi-stage: build Vue widget → serve demo page + widget via nginx
FROM node:22-alpine AS build

WORKDIR /build
COPY apps/widget/package.json apps/widget/package-lock.json ./
RUN npm ci
COPY apps/widget/ ./
RUN npm run build

FROM nginx:1.27-alpine

COPY apps/widget/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist/ /usr/share/nginx/html/widget/
COPY sample-site/ /usr/share/nginx/html/

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
