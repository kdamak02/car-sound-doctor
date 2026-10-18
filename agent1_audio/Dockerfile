FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Real mode (MOCK_MODE=false) needs the model stack: build with  AGENT1_REAL=true docker compose build
ARG WITH_MODEL=false
RUN if [ "$WITH_MODEL" = "true" ]; then \
      pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
      pip install --no-cache-dir transformers soundfile librosa scikit-learn joblib onnxruntime ; fi
COPY src ./src
COPY service ./service
COPY vad ./vad
ENV CONTRACTS_DIR=/app/contracts
EXPOSE 8001
CMD ["uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8001"]
