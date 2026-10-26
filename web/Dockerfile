# Multi-stage production build for JackVerse Caseworker Web UI
FROM node:22-alpine AS builder

WORKDIR /app

# Build arguments for Vite compilation
ARG VITE_JACKVERSE_DEV_AUTH=false
ARG VITE_ENABLE_PROTOTYPES=false
ARG VITE_CASEWORKER_API_URL=/api

ENV VITE_JACKVERSE_DEV_AUTH=$VITE_JACKVERSE_DEV_AUTH
ENV VITE_ENABLE_PROTOTYPES=$VITE_ENABLE_PROTOTYPES
ENV VITE_CASEWORKER_API_URL=$VITE_CASEWORKER_API_URL

# Copy dependency specifications
COPY package.json package-lock.json* ./

# Install clean dependencies
RUN npm ci

# Copy full frontend source
COPY . .

# Compile TypeScript and Vite static bundle
RUN npm run build

# Stage 2: Production Nginx server with TLS support
FROM nginx:alpine

# Generate default self-signed TLS certificate for localhost development/testing
RUN apk add --no-cache openssl && \
    mkdir -p /etc/nginx/ssl && \
    openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout /etc/nginx/ssl/localhost.key \
    -out /etc/nginx/ssl/localhost.crt \
    -subj "/CN=localhost"

# Copy nginx SPA routing configuration
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy build artifacts to nginx public root
COPY --from=builder /app/dist /usr/share/nginx/html

# Copy entrypoint script for dynamic TLS fallback
COPY docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh

EXPOSE 80 443

ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]
