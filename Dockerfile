FROM python:3.11-slim

# --- Установка зависимостей ---
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    build-essential \
    pkg-config \
    cmake \
    curl \
    git \
    ffmpeg \
    v4l-utils \
    libusb-1.0-0-dev \
    libssl-dev \
    libgl1 \
    libglib2.0-dev \
    libglfw3-dev \
    libgl1-mesa-dev \
    libglu1-mesa-dev \
    python3-dev \
    python3-setuptools \
    python3-wheel \
    python3-numpy \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- Сборка librealsense + pyrealsense2 ---
RUN git clone https://github.com/IntelRealSense/librealsense.git && \
    cd librealsense && \
    mkdir build && cd build && \
    cmake .. \
        -DBUILD_PYTHON_BINDINGS=bool:true \
        -DPYTHON_EXECUTABLE=$(which python3) \
        -DBUILD_EXAMPLES=false \
        -DBUILD_GRAPHICAL_EXAMPLES=false \
        -DCMAKE_BUILD_TYPE=Release && \
    make -j$(nproc) && \
    make install && \
    ldconfig && \
    cd / && rm -rf /librealsense

# --- Python requirements (after librealsense is built) ---
COPY requirements.txt .
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt && \
    python3 -c "import pyrealsense2; print('pyrealsense2 version:', getattr(pyrealsense2, '__version__', 'installed'))"

# --- Копируем код сервиса ---
WORKDIR /app
COPY . /app

# Экспонируем порт
EXPOSE 8104

# Переменные окружения по умолчанию
ENV CAMERA_WIDTH=640
ENV CAMERA_HEIGHT=480
ENV CAMERA_FPS=30
ENV CAMERA_ROTATION=180
ENV SERVICE_HOST=0.0.0.0
ENV SERVICE_PORT=8104

# Healthcheck внутри контейнера
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -f http://localhost:8104/ || exit 1

# Запуск uvicorn без reload (production mode)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8104"]