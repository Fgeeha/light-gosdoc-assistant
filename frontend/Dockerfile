FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
# Шаблон превращается в conf.d/default.conf при старте; адрес прокси — из окружения.
ENV TRUSTED_PROXY=127.0.0.1
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY security-headers.inc /etc/nginx/security-headers.inc
COPY proxy-backend.inc /etc/nginx/proxy-backend.inc
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
