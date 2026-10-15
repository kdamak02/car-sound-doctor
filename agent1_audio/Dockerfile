FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src
COPY service ./service
ENV CONTRACTS_DIR=/app/contracts
EXPOSE 8001
CMD ["uvicorn", "service.app:app", "--host", "0.0.0.0", "--port", "8001"]
