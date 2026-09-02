# This Dockerfile builds the MinIO community server from the exact source
# commit recorded in `../../images.lock.yaml`.  It deliberately does not use
# the retired `quay.io/minio/minio` runtime image: the resulting server binary
# is compiled locally and the final image digest is locked only after a build.

# `BUILDPLATFORM` runs the Go compiler natively on the builder host, while the
# automatic `TARGETOS` and `TARGETARCH` arguments produce a static binary for
# the image platform requested by `docker buildx`.  The immediate local target
# is linux/arm64 because k3d's nodes run on Apple Silicon.
FROM --platform=$BUILDPLATFORM docker.io/library/golang@sha256:4ed690d6649d63c312b99a6120025ec79ce3b542968a37da53d6236c7c61a848 AS builder

ARG TARGETOS
ARG TARGETARCH

WORKDIR /src

# Fetch only the locked commit, then verify the checked-out object before any
# dependency or compiler command runs.  `git` is included by the locked Go
# bookworm builder image.  The repository and commit are constants rather than
# build arguments, preventing a normal build invocation from substituting an
# unreviewed source input.
RUN git init . \
    && git remote add origin https://github.com/minio/minio.git \
    && git fetch --depth=1 origin 9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a \
    && git checkout --detach FETCH_HEAD \
    && test "$(git rev-parse HEAD)" = 9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a

# Download modules from the dependency versions protected by the source
# release's go.sum before compiling.  No application source is copied from the
# local workspace, so this build context cannot accidentally include secrets or
# uncommitted CloudDSP files.
RUN go mod download

# This mirrors MinIO's release Makefile: a static Linux binary with the `kqueue`
# build tag, trimmed source paths, and upstream-generated linker metadata.  The
# explicit `MINIO_RELEASE=RELEASE` prevents a source build from being labelled
# `DEVELOPMENT` at runtime.  The binary is written outside the source tree so
# the final stage copies one file.
RUN LDFLAGS="$(MINIO_RELEASE=RELEASE go run buildscripts/gen-ldflags.go)" \
    && CGO_ENABLED=0 GOOS="${TARGETOS}" GOARCH="${TARGETARCH}" \
       go build -tags kqueue -trimpath -ldflags "${LDFLAGS}" \
       -o /out/minio

# The runtime stage is non-root and shell-free.  A future StatefulSet will give
# its `/data` PVC the matching filesystem group, while users diagnose MinIO
# through a separate client Job rather than installing tools in this server.
FROM gcr.io/distroless/static-debian12@sha256:afa5c872c891853ca7fcf1f12c3edb23f7eeef36189728842dd51042ff57f7ab

LABEL org.opencontainers.image.title="CloudDSP local MinIO" \
      org.opencontainers.image.description="Locally built MinIO community server for private CloudDSP object storage" \
      org.opencontainers.image.source="https://github.com/minio/minio" \
      org.opencontainers.image.revision="9e49d5e7a648f00e26f2246f4dc28e6b07f8c84a" \
      org.opencontainers.image.version="RELEASE.2025-10-15T17-29-55Z"

COPY --from=builder --chown=65532:65532 /out/minio /usr/local/bin/minio

# The data directory is supplied by a Kubernetes PVC.  Port 9000 is the S3 API
# and 9001 is MinIO's administrative console; `EXPOSE` documents them only and
# does not publish either port outside the container or cluster.
USER 65532:65532
VOLUME ["/data"]
EXPOSE 9000 9001
ENTRYPOINT ["/usr/local/bin/minio"]
CMD ["server", "/data", "--console-address", ":9001"]
