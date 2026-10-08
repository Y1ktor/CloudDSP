# syntax=docker/dockerfile:1.7
# Native Linux ARM64 MuseScore matches the tested macOS version. Verify the
# official release digest before extraction; no packages/downloads at runtime.
FROM docker.io/library/python@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 QT_QPA_PLATFORM=offscreen \
    XDG_RUNTIME_DIR=/tmp/runtime HOME=/home/midi-sheet
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates xvfb xauth \
    libgpg-error0 libjack-jackd2-0 libpipewire-0.3-0 libxcb-cursor0 libxkbcommon-x11-0 libglib2.0-0 libgl1 libegl1 libopengl0 libasound2t64 libnss3 libxcomposite1 \
    libxdamage1 libxrandr2 libxkbcommon0 libdbus-1-3 libfontconfig1 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fL --retry 3 https://github.com/musescore/MuseScore/releases/download/v4.7.5/MuseScore-Studio-4.7.5.260831071-aarch64.AppImage -o /tmp/musescore.AppImage \
    && echo '034f0257fd21ed6b7d4863714dbf8e97cc86d454beb79aaa845a5ade3ff99e36  /tmp/musescore.AppImage' | sha256sum -c - \
    && chmod +x /tmp/musescore.AppImage && cd /opt && /tmp/musescore.AppImage --appimage-extract >/dev/null \
    && rm /tmp/musescore.AppImage
WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
RUN groupadd --gid 10007 midi-sheet && useradd --uid 10007 --gid midi-sheet --create-home --shell /usr/sbin/nologin midi-sheet
COPY app ./app
# Bind tests only for validation; no test sources ship in the image.
RUN --mount=type=bind,source=tests,target=/tests python -m unittest discover -s /tests -v
USER 10007:10007
ENTRYPOINT ["python", "-m", "app.worker"]
