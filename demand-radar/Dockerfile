FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY demand_radar ./demand_radar
COPY sources.toml .
COPY eval ./eval
COPY docker/entrypoint.py /entrypoint.py

ENTRYPOINT ["python", "/entrypoint.py"]
