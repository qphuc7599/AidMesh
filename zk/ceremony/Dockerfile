FROM node:22.17.0-bookworm-slim@sha256:b04ce4ae4e95b522112c2e5c52f781471a5cbc3b594527bcddedee9bc48c03a0

LABEL org.opencontainers.image.title="AirProof pinned snarkjs ceremony runner"
LABEL org.opencontainers.image.version="0.7.6"
LABEL org.opencontainers.image.description="Single-operator, single-host workflow simulation only"

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts \
    && test "$(node -p "require('./node_modules/snarkjs/package.json').version")" = "0.7.6" \
    && npm cache clean --force \
    && mkdir /inputs

COPY zk/ceremony/contribute.mjs /app/contribute.mjs

USER node

CMD ["node", "/app/node_modules/snarkjs/build/cli.cjs", "--help"]
