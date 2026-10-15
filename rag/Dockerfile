FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . ./rag
ENV CONTRACTS_DIR=/app/contracts
EXPOSE 8002
CMD ["uvicorn", "rag.service.app:app", "--host", "0.0.0.0", "--port", "8002"]
