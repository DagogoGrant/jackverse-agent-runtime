# Multi-stage production build for JackVerse Caseworker Web UI
FROM node:22-alpine AS builder

WORKDIR /app

# Copy dependency specifications
COPY package.json package-lock.json* ./

# Install clean dependencies
RUN npm ci

# Copy full frontend source
COPY . .

# Compile TypeScript and Vite static bundle
RUN npm run build

# Stage 2: Production Nginx server
FROM nginx:alpine

# Copy nginx SPA routing configuration
RUN rm /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Copy build artifacts to nginx public root
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
