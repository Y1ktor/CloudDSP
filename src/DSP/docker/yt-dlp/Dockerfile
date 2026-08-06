ARG LAMBDA_PLATFORM=linux/amd64
FROM --platform=${LAMBDA_PLATFORM} public.ecr.aws/lambda/python:3.11

# Pin and verify FFmpeg, and keep extraction tools in the same layer so they
# do not remain in the final Lambda image. A system-wide yum update makes builds
# nondeterministic and adds packages the function does not need.
ARG FFMPEG_VERSION=6.0.1
ARG FFMPEG_SHA256=28268bf402f1083833ea269331587f60a242848880073be8016501d864bd07a5
RUN yum install -y --setopt=install_weak_deps=False tar xz && \
    curl -fsSLo ffmpeg.tar.xz "https://www.johnvansickle.com/ffmpeg/old-releases/ffmpeg-${FFMPEG_VERSION}-amd64-static.tar.xz" && \
    echo "${FFMPEG_SHA256}  ffmpeg.tar.xz" | sha256sum -c - && \
    tar -xJf ffmpeg.tar.xz && \
    install -m 0755 "ffmpeg-${FFMPEG_VERSION}-amd64-static/ffmpeg" /usr/local/bin/ffmpeg && \
    install -m 0755 "ffmpeg-${FFMPEG_VERSION}-amd64-static/ffprobe" /usr/local/bin/ffprobe && \
    rm -rf "ffmpeg-${FFMPEG_VERSION}-amd64-static" ffmpeg.tar.xz && \
    yum remove -y tar xz && \
    yum clean all && \
    rm -rf /var/cache/yum

# Install one pinned yt-dlp distribution. Lambda's Python base image already
# supplies boto3, so installing it again only increases the image size.
COPY docker/yt-dlp/requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN pip install --no-cache-dir -r ${LAMBDA_TASK_ROOT}/requirements.txt

# Copy the lambda function handler
COPY src/Cloud/LambdaYtDlp.py ${LAMBDA_TASK_ROOT}/LambdaYtDlp.py

# Set the CMD to your handler
CMD ["LambdaYtDlp.lambda_handler"]
