# syntax=docker/dockerfile:1.7
# Apple Silicon local profile: CPU inference in a digest-pinned ARM64 base.
# Model checkpoints and RapidOCR weights are downloaded during the image build;
# a Pod never needs public internet or writable package directories.
FROM docker.io/library/python@sha256:528257d48c1da0dcecc2e725d1ae34498d60c965f1241e39cd6a85a8859bdf84

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt ./requirements.txt
# rapidocr pulls a second, GUI OpenCV wheel whose ARM64 binary crashes on
# this node. Keep only homr's reviewed headless OpenCV implementation.
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip uninstall -y opencv-python \
    && python -m pip install --no-cache-dir --no-deps --force-reinstall \
       opencv-python-headless==4.14.0.94
RUN python -c 'import numpy; print("numpy ready")' \
    && python -c 'import cv2; print("opencv ready")' \
    && python -c 'import onnxruntime; print("onnx ready")' \
    && homr --init \
    && python -c 'import homr, music21, mido, psycopg, boto3, pika'
COPY app ./app
RUN groupadd --system --gid 10007 score-omr && useradd --system --uid 10007 \
    --gid score-omr --create-home --home-dir /home/score-omr --shell /usr/sbin/nologin score-omr
USER 10007:10007
ENTRYPOINT ["python", "-m", "app.worker"]
