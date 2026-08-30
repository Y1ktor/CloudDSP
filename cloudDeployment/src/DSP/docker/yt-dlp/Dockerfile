ARG LAMBDA_PLATFORM=linux/amd64
FROM --platform=${LAMBDA_PLATFORM} denoland/deno:bin-2.8.2 AS deno

# Python 3.12 Lambda images use Amazon Linux 2023. Current Deno releases
# require a newer glibc than the Python 3.11/Amazon Linux 2 base provides.
FROM --platform=${LAMBDA_PLATFORM} public.ecr.aws/lambda/python:3.12

# Pin and verify FFmpeg, and keep extraction tools in the same layer so they
# do not remain in the final Lambda image. A system-wide package-manager update makes builds
# nondeterministic and adds packages the function does not need.
ARG FFMPEG_VERSION=6.0.1
ARG FFMPEG_SHA256=28268bf402f1083833ea269331587f60a242848880073be8016501d864bd07a5
RUN dnf install -y tar xz && \
    curl -fsSLo ffmpeg.tar.xz "https://www.johnvansickle.com/ffmpeg/old-releases/ffmpeg-${FFMPEG_VERSION}-amd64-static.tar.xz" && \
    echo "${FFMPEG_SHA256}  ffmpeg.tar.xz" | sha256sum -c - && \
    tar -xJf ffmpeg.tar.xz && \
    install -m 0755 "ffmpeg-${FFMPEG_VERSION}-amd64-static/ffmpeg" /usr/local/bin/ffmpeg && \
    install -m 0755 "ffmpeg-${FFMPEG_VERSION}-amd64-static/ffprobe" /usr/local/bin/ffprobe && \
    rm -rf "ffmpeg-${FFMPEG_VERSION}-amd64-static" ffmpeg.tar.xz && \
    dnf remove -y tar xz && \
    dnf clean all && \
    rm -rf /var/cache/dnf

# Current YouTube downloads require yt-dlp's external JavaScript challenge
# solver. Copy only the Deno binary from its official minimal image; its cache
# lives in Lambda's writable /tmp directory rather than the read-only runtime.
COPY --from=deno /deno /usr/local/bin/deno
ENV DENO_DIR=/tmp/deno

# Install one pinned yt-dlp distribution. Its ``default`` extra includes the
# version-matched yt-dlp-ejs challenge scripts; ``curl-cffi`` enables browser
# impersonation. Lambda's Python base image already supplies boto3, so
# installing it again only increases the image size.
COPY docker/yt-dlp/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Copy the durable-job helper and Lambda handler. The handler writes the
# existing uploads/{job_id}/... key so the S3 EventBridge rule can submit
# Demucs without a separate callback route.
COPY src/Cloud/cloud_job_workflow.py ${LAMBDA_TASK_ROOT}/cloud_job_workflow.py
COPY src/Cloud/media_url_policy.py ${LAMBDA_TASK_ROOT}/media_url_policy.py
COPY src/Cloud/LambdaYtDlp.py ${LAMBDA_TASK_ROOT}/LambdaYtDlp.py

# Set the CMD to your handler
CMD ["LambdaYtDlp.lambda_handler"]
