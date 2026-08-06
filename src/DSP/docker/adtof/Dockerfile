# CPU-only ADTOF Lambda for multi-instrument drum transcription.
ARG LAMBDA_PLATFORM=linux/amd64
FROM --platform=${LAMBDA_PLATFORM} public.ecr.aws/lambda/python:3.11

# Keep native-code and numerical-library caches in Lambda's writable storage.
ENV NUMBA_CACHE_DIR=/tmp/numba-cache \
    NUMBA_NUM_THREADS=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    PYTHONDONTWRITEBYTECODE=1

# FFmpeg is retained for non-WAV inputs. Git is only needed to fetch a pinned
# ADTOF revision and is removed after installation.
RUN yum install -y git gzip tar xz && \
    yum clean all && \
    rm -rf /var/cache/yum

ARG FFMPEG_VERSION=6.0.1
ARG FFMPEG_SHA256=28268bf402f1083833ea269331587f60a242848880073be8016501d864bd07a5
RUN curl -fsSLo ffmpeg.tar.xz "https://www.johnvansickle.com/ffmpeg/old-releases/ffmpeg-${FFMPEG_VERSION}-amd64-static.tar.xz" && \
    echo "${FFMPEG_SHA256}  ffmpeg.tar.xz" | sha256sum -c - && \
    tar -xJf ffmpeg.tar.xz && \
    install -m 0755 "ffmpeg-${FFMPEG_VERSION}-amd64-static/ffmpeg" /usr/local/bin/ffmpeg && \
    install -m 0755 "ffmpeg-${FFMPEG_VERSION}-amd64-static/ffprobe" /usr/local/bin/ffprobe && \
    rm -rf "ffmpeg-${FFMPEG_VERSION}-amd64-static" ffmpeg.tar.xz

# ADTOF's PyTorch port needs only Torch, Librosa, and PrettyMIDI. Use the
# PyTorch CPU wheel index so no CUDA runtime is included in the Lambda image.
RUN pip install --no-cache-dir \
        "numpy==1.26.4" \
        "scipy==1.13.1" \
        "scikit-learn==1.5.1" \
        "numba==0.60.0" \
        "llvmlite==0.43.0" \
        "soundfile==0.12.1" \
        "soxr==0.3.7" \
        "librosa==0.10.2.post1" \
        "pretty_midi==0.2.10" && \
    pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu \
        "torch==2.5.1+cpu"

ARG ADTOF_COMMIT=85c192e78f716ea0b111cc8a5ee4a8f6a3a4f8a9
RUN git clone https://github.com/xavriley/ADTOF-pytorch.git /tmp/adtof && \
    git -C /tmp/adtof checkout --detach "${ADTOF_COMMIT}" && \
    pip install --no-cache-dir --no-deps /tmp/adtof && \
    python -c "from adtof_pytorch import transcribe_to_midi; print('ADTOF import succeeded')" && \
    rm -rf /tmp/adtof && \
    yum remove -y git && \
    yum clean all && \
    rm -rf /var/cache/yum

COPY src/Cloud/cloud_job_workflow.py ${LAMBDA_TASK_ROOT}/cloud_job_workflow.py
COPY src/Cloud/LambdaMIDIADTOF.py ${LAMBDA_TASK_ROOT}/ADTOF.py

CMD ["ADTOF.lambda_handler"]
