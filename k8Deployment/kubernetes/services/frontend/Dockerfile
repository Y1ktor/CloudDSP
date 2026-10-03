# syntax=docker/dockerfile:1
#
# This image has two separate responsibilities:
#   1. The official Node builder compiles the shared React source's local profile.
#   2. The official NGINX runtime serves only those generated files.
#
# No Node modules, source code, environment file, or build tool is copied into
# the final runtime image. Exact immutable base references are recorded in
# ../../images.lock.yaml under images.frontend-node-builder and
# images.frontend-nginx-runtime.

FROM docker.io/library/node@sha256:ba849c60be29959425b8734d57b8b4b7d56f98edd9504c9af091d5281095a71e AS builder

WORKDIR /src/app

# These arguments carry public browser configuration from the explicitly named
# ignored k8Deployment/.local/frontend.env.production file. They are not
# credentials: Vite embeds VITE_* values in the JavaScript sent to every browser.
# Only the allowlisted source paths below are copied from the named context;
# host environment files and generated output never enter an image layer.
ARG VITE_OIDC_ISSUER
ARG VITE_OIDC_CLIENT_ID
ARG VITE_OIDC_REDIRECT_URI
ARG VITE_OIDC_POST_LOGOUT_REDIRECT_URI
ARG VITE_JOB_API_URL
ARG VITE_OBJECT_STORAGE_URL
ARG VITE_WEBSOCKET_URL
ARG VITE_DEMO_ASSET_ORIGIN
ENV VITE_OIDC_ISSUER=${VITE_OIDC_ISSUER} \
    VITE_OIDC_CLIENT_ID=${VITE_OIDC_CLIENT_ID} \
    VITE_OIDC_REDIRECT_URI=${VITE_OIDC_REDIRECT_URI} \
    VITE_OIDC_POST_LOGOUT_REDIRECT_URI=${VITE_OIDC_POST_LOGOUT_REDIRECT_URI} \
    VITE_JOB_API_URL=${VITE_JOB_API_URL} \
    VITE_OBJECT_STORAGE_URL=${VITE_OBJECT_STORAGE_URL} \
    VITE_WEBSOCKET_URL=${VITE_WEBSOCKET_URL} \
    VITE_DEMO_ASSET_ORIGIN=${VITE_DEMO_ASSET_ORIGIN}

# Copy the lockfile before application source so dependency installation remains
# cached when only React code changes. `npm ci` rejects a package.json/lockfile
# mismatch and creates a repeatable dependency tree without lifecycle scripts.
# The primary context owns only local NGINX delivery configuration. BuildKit's
# named `frontend` context points at the one repository-root frontend/ tree.
# Copy dependency inputs first to retain the npm cache for source-only edits.
COPY --from=frontend package.json package-lock.json ./
RUN npm ci --ignore-scripts

# Explicit paths also protect the image if a BuildKit version does not apply
# ignore rules to additional local contexts. Never COPY the whole source root:
# it can contain developer .env files, node_modules, and earlier dist outputs.
COPY --from=frontend src/ ./src/
COPY --from=frontend public/ ./public/
COPY --from=frontend index.html csp.js vite.config.js ./
RUN npm run build:local

# The NGINX official image is the final runtime. Pinning the index digest lets
# Docker select arm64 locally and amd64 in a future Linux profile while keeping
# both selections tied to the reviewed upstream OCI index.
FROM docker.io/library/nginx@sha256:a8b39bd9cf0f83869a2162827a0caf6137ddf759d50a171451b335cecc87d236 AS runtime

LABEL org.opencontainers.image.title="CloudDSP local frontend" \
      org.opencontainers.image.description="Static local React frontend with Keycloak OIDC" \
      org.opencontainers.image.source="frontend" \
      org.opencontainers.image.version="0.6.0-shared-profiles"

# Replace the root-oriented default configuration with an explicit unprivileged
# listener, then copy only Vite's immutable output into NGINX's document root.
COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=builder --chown=nginx:nginx /src/app/dist/local/ /usr/share/nginx/html/
# Vite writes this generated NGINX include from the same csp.js policy used by
# index.html. Keeping it outside the document root prevents it being served as
# a browser asset while `include` below makes NGINX send the matching header.
COPY --from=builder /src/app/dist/local/csp-header.conf /etc/nginx/csp-header.conf

# Validate the exact final NGINX configuration and generated header include
# during the image build, before a malformed CSP could reach the local registry.
RUN nginx -t

# The official image supplies this ordinary runtime account. Binding 8080 rather
# than privileged port 80 lets a future Pod enforce runAsNonRoot cleanly.
USER nginx
EXPOSE 8080
ENTRYPOINT ["nginx"]
CMD ["-c", "/etc/nginx/nginx.conf", "-g", "daemon off;"]
