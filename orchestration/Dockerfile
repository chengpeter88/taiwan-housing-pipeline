FROM apache/airflow:3.4.0-python3.12
USER root
COPY requirements.lock /tmp/housing-requirements.lock
COPY orchestration/requirements-runtime.txt /tmp/housing-runtime.txt
# uv 從 PyPI 安裝並固定版本；不依賴 ghcr.io 映像，避免主機上過期的 registry 憑證造成拉取失敗。
RUN python -m venv /opt/uv-tool \
    && /opt/uv-tool/bin/pip install --no-cache-dir uv==0.13.6 \
    && /opt/uv-tool/bin/uv venv /opt/housing-venv --python /usr/local/bin/python \
    && /opt/uv-tool/bin/uv pip install --no-cache --python /opt/housing-venv/bin/python \
       --constraint /tmp/housing-requirements.lock -r /tmp/housing-runtime.txt
USER airflow
