FROM debian:bookworm-slim

# Install system dependencies required for Python, PyInstaller, and Chromium libs discovery
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ca-certificates \
    python3 \
    python3-venv \
    python3-pip \
    python3-dev \
    build-essential \
    patchelf \
    git \
    && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build

# Create an isolated Python environment to avoid PEP 668 (externally managed)
RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

# Install Python dependencies into the venv
RUN python -m pip install --no-cache-dir --upgrade pip && \
    python -m pip install --no-cache-dir pyinstaller playwright

# Ensure Playwright installs Chromium into package path and installs runtime deps
ENV PLAYWRIGHT_BROWSERS_PATH=0
RUN PLAYWRIGHT_BROWSERS_PATH=0 playwright install
RUN PLAYWRIGHT_BROWSERS_PATH=0 playwright install-deps

# Copy build scripts
COPY build.py /build/
COPY main.py /build/

# Run the build using the venv Python
RUN python build.py && \
    mkdir -p /out && \
    cp -v dist/main /out/main && \
    chmod +x /out/main

# Not shipping this image; keep single-stage and expose artifact at /out
CMD ["/bin/sh", "-lc", "ls -l /out && echo Artifact: /out/main"]
