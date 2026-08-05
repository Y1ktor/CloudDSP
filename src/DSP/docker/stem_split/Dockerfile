# Base Image: We use the official PyTorch image. 
# It comes pre-loaded with Python, PyTorch, and NVIDIA CUDA drivers.
# We use 2.4.0-cuda12.1-cudnn9 to natively support torchcodec and utilize the latest ECS GPU drivers.
FROM pytorch/pytorch:2.4.0-cuda12.1-cudnn9-runtime

# Set the working directory inside the container
WORKDIR /app

# Install OS-level dependencies
# FFmpeg is strictly required by Demucs to read/write audio files (mp3, wav, etc.)
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# - demucs: The core stem splitting ML library
# - boto3: The AWS SDK to download/upload from S3
# - torchcodec & soundfile: Required by newer versions of torchaudio/demucs for reading/writing audio
RUN pip install --no-cache-dir demucs boto3 torchcodec soundfile

# ==========================================
# COLD START PREVENTION (Model Weight Caching)
# ==========================================
# Set a stable cache directory for PyTorch models so we know exactly where they live
ENV TORCH_HOME=/app/models

# Use the Demucs Python API to fetch the models directly. 
# This is highly efficient as it purely downloads the weights without forcing a dummy CPU inference cycle.
RUN python -c "from demucs.pretrained import get_model; get_model('htdemucs'); get_model('htdemucs_6s')"

# Copy the Python Cloud wrapper script into the container
# NOTE: When building this image, the build context must be the `src/DSP` folder.
# Run this command from src/DSP: 
# docker build -t clouddsp-demucs -f docker/stem_split/Dockerfile .
COPY src/Cloud/BatchDemucs.py /app/BatchDemucs.py

# The command that runs exactly when AWS Batch starts the container.
# AWS Batch will pass the required environment variables (INPUT_BUCKET, etc.) behind the scenes.
ENTRYPOINT ["python", "/app/BatchDemucs.py"]
