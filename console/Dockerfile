# --- build ---
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY tsconfig.json vite.config.ts index.html ./
COPY src/ ./src/
COPY public/ ./public/

# Baked at build time: a static bundle cannot read the environment at runtime.
ARG VITE_VERIFIER_URL=http://localhost:8000
ARG VITE_BIFROST_URL=http://localhost:8080
ENV VITE_VERIFIER_URL=$VITE_VERIFIER_URL VITE_BIFROST_URL=$VITE_BIFROST_URL
RUN npm run build

# --- serve ---
FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
HEALTHCHECK --interval=10s --timeout=5s --start-period=5s --retries=6 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
