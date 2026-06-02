FROM python:3.12-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgomp1 libgl1 libglib2.0-0 libxcb1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# PyTorch CPU (evita descargar CUDA ~2GB dentro del contenedor)
RUN pip install --no-cache-dir --default-timeout=300 --retries 5 \
    torch torchvision --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir --default-timeout=300 --retries 5 -r requirements.txt \
    && pip uninstall -y opencv-python 2>/dev/null || true \
    && pip install --no-cache-dir opencv-python-headless

COPY . .
RUN sed -i 's/\r$//' docker-entrypoint.sh && chmod +x docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/bin/sh", "/app/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
