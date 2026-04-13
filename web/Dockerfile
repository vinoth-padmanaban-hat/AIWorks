# Next.js admin UI — build from repository root:
#   docker build -f web/Dockerfile --build-arg NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 -t aiworks-web .
# syntax=docker/dockerfile:1

FROM node:20-bookworm-slim AS deps
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci

FROM node:20-bookworm-slim AS builder
WORKDIR /web
COPY web/ ./
COPY --from=deps /web/node_modules ./node_modules

ARG NEXT_PUBLIC_API_URL=http://127.0.0.1:8000
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
ENV NEXT_TELEMETRY_DISABLED=1

RUN npm run build

FROM node:20-bookworm-slim AS runner
WORKDIR /web

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

COPY --from=builder /web/public ./public
COPY --from=builder --chown=nextjs:nodejs /web/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /web/.next/static ./.next/static

USER nextjs
EXPOSE 3000

CMD ["node", "server.js"]
