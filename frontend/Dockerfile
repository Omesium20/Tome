# ============================================
# Stage 1: install dependencies
# ============================================
FROM node:20-slim AS deps

WORKDIR /app

COPY package.json package-lock.json ./

RUN npm ci

# ============================================
# Stage 2: build the app
# ============================================
FROM node:20-slim AS builder

WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .

ENV NODE_ENV=production

# Vite inlines import.meta.env.VITE_* into the JS bundle at build time rather
# than reading it at container startup — so these must arrive as build args
# here, not as `environment:` entries on the service in Dockercompose.yaml.
ARG VITE_API_BASE_URL
ARG VITE_USE_MOCKS
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
ENV VITE_USE_MOCKS=${VITE_USE_MOCKS}

RUN npm run build

# ============================================
# Stage 3: serve the static build
# ============================================
# Vite emits a plain static bundle with no server component, so this is nginx
# rather than a Node runtime — a much smaller final image.
FROM nginx:alpine AS runner

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 3000

CMD ["nginx", "-g", "daemon off;"]
