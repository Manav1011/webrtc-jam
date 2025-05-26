FROM python:3.10-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    pulseaudio-utils \
    alsa-utils \
    libasound2 \
    libasound2-dev \
    libpulse-dev \
    portaudio19-dev \
    ffmpeg \
    build-essential \
    gcc \
    git \
    curl \
    vim \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Create necessary directories
RUN mkdir -p /app/templates /app/static

# Copy application files
COPY driver.py /app/
COPY templates/ /app/templates/
COPY static/ /app/static/
COPY .env /app/

# Set permissions
RUN chmod +x driver.py

# Install Python dependencies
RUN pip install --no-cache-dir \
    fastapi \
    uvicorn \
    websockets \
    aiortc \
    numpy \
    python-dotenv \
    jinja2

# The port will be specified by the environment variable PORT
ARG PORT=8000
ENV PORT=${PORT}
EXPOSE ${PORT}

# Start the FastAPI application
CMD uvicorn driver:app --host 0.0.0.0 --port ${PORT}
