FROM node:22-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WEB_HOST=0.0.0.0 \
    WEB_PORT=5000 \
    PI_CODING_COMMAND=pi \
    PATENT_AGENT_DATA_DIR=/data \
    PATENT_AGENT_USER_CONFIG=/data/user_config.json \
    TZ=Asia/Shanghai

WORKDIR /app

RUN set -eux; \
    for attempt in 1 2 3 4 5; do \
        apt-get update -o Acquire::Retries=5 \
        && apt-get install -y --no-install-recommends --fix-missing -o Acquire::Retries=5 \
            ca-certificates \
            python3 \
            python3-pip \
            python3-venv \
        && break; \
        if [ "$attempt" = "5" ]; then exit 1; fi; \
        echo "apt failed, retrying in 8 seconds..."; \
        sleep 8; \
        rm -rf /var/lib/apt/lists/*; \
    done; \
    rm -rf /var/lib/apt/lists/*

RUN npm install -g @earendil-works/pi-coding-agent

COPY requirements.txt .
RUN python3 -m pip install --break-system-packages --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data/outputs /data/feishu /app/outputs

VOLUME ["/data", "/app/outputs"]

EXPOSE 5000

CMD ["python3", "app.py"]
