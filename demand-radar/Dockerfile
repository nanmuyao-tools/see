FROM python:3.12-slim

WORKDIR /app

# 不缓冲标准输出：否则 docker compose up 时看不到逐条进度，直到跑完才一起打印
ENV PYTHONUNBUFFERED=1

# 先只装依赖（按锁文件的精确版本），代码改动不会让这一层缓存失效
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock

COPY pyproject.toml README.md LICENSE ./
COPY demand_radar ./demand_radar
COPY sources.toml models.toml ./
COPY eval ./eval
# 装本项目本身（依赖已就位，--no-deps 保证不会偷偷升级），得到 demand-radar 命令
RUN pip install --no-cache-dir --no-deps .
COPY docker/entrypoint.py /entrypoint.py

# 以普通用户运行。uid 1000 是大多数 Linux 桌面的第一个用户，挂载的 ./out 一般直接可写；
# 不一致时入口脚本会提示怎么修（见 docker/entrypoint.py 的 check_output_dir）
RUN useradd --uid 1000 --create-home app && mkdir -p /app/out && chown app:app /app/out
USER app

ENTRYPOINT ["python", "/entrypoint.py"]
