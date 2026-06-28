# Base Image: Basic Pitch is built on TensorFlow, not PyTorch.
# We use the official TensorFlow Docker image as our base.
# Since Basic Pitch inference is extremely fast, many users run it on cheap CPU instances.
# If you intend to use GPUs for Basic Pitch, you would use tensorflow:2.12.0-gpu
FROM tensorflow/tensorflow:2.12.0

WORKDIR /app

# Install OS-level dependencies
# Basic Pitch relies on the `soundfile` python library to read audio, 
# which requires `libsndfile1` at the OS level.
RUN apt-get update && \
    apt-get install -y --no-install-recommends libsndfile1 && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
# - basic-pitch: Spotify's Audio-to-MIDI library
# - boto3: AWS SDK for S3 interaction
RUN pip install --no-cache-dir basic-pitch boto3

# Note on Cold Starts:
# Unlike Demucs, Spotify actually bundled the Basic Pitch ML weights (ICASSP_2022) 
# directly inside the pip package itself! 
# Because it's packaged within the library, there is ZERO cold-start download time.
# The weights are already inside this Docker image natively.

# Copy the Python Cloud wrapper script into the container
# NOTE: When building this image, the build context must be the `src/DSP` folder.
# Run this command from src/DSP: 
# docker build -t clouddsp-basicpitch -f docker/basic_pitch/Dockerfile .
COPY src/Cloud/CloudExtractBasicPitch.py /app/CloudExtractBasicPitch.py

ENTRYPOINT ["python", "/app/CloudExtractBasicPitch.py"]
