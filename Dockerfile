FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY livemap ./livemap
COPY docs ./docs
COPY scripts ./scripts
ENV LIVEMAP_HOST=0.0.0.0 \
    LIVEMAP_PORT=8765 \
    LIVEMAP_OPEN_BROWSER=0 \
    PYTHONUNBUFFERED=1
EXPOSE 8765
CMD ["python", "-m", "livemap"]
