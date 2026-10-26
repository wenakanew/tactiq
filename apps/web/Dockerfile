FROM node:22-alpine AS build
WORKDIR /app
ARG TACTIQ_BACKEND_URL
ENV TACTIQ_BACKEND_URL=$TACTIQ_BACKEND_URL
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-alpine
WORKDIR /app
ENV NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
RUN addgroup -g 10001 tactiq && adduser -D -u 10001 -G tactiq tactiq
COPY --from=build --chown=tactiq:tactiq /app/.next/standalone ./
COPY --from=build --chown=tactiq:tactiq /app/.next/static ./.next/static
USER tactiq
EXPOSE 3000
CMD ["node", "server.js"]
