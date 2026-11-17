FROM ghcr.io/astral-sh/uv:0.13.6 AS uv
FROM apache/airflow:3.4.0-python3.12
USER root
COPY --from=uv /uv /usr/local/bin/uv
COPY requirements.lock /tmp/housing-requirements.lock
COPY orchestration/requirements-runtime.txt /tmp/housing-runtime.txt
RUN uv venv /opt/housing-venv --python /usr/local/bin/python \
    && uv pip install --python /opt/housing-venv/bin/python \
       --constraint /tmp/housing-requirements.lock -r /tmp/housing-runtime.txt
USER airflow
