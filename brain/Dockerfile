FROM python:3.11-slim
WORKDIR /app
COPY brain/ ./
RUN pip install --no-cache-dir -e .
EXPOSE 8500
CMD ["uvicorn", "qwickguard_brain.main:app", "--host", "0.0.0.0", "--port", "8500"]
