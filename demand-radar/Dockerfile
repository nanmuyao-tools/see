FROM python:3.12-slim

WORKDIR /app

# 不缓冲标准输出：否则 docker compose up 时看不到逐条进度，直到跑完才一起打印
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY demand_radar ./demand_radar
COPY sources.toml .
COPY eval ./eval
COPY docker/entrypoint.py /entrypoint.py

ENTRYPOINT ["python", "/entrypoint.py"]
