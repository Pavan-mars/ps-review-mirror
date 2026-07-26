# BYOC image for the PS3 two-head endpoint (severity + root-cause).
# Build via SageMaker Studio Image Build / sm-docker / CodeBuild (CloudShell has no docker daemon):
#   sm-docker build . --repository cubic-pdm/mars-ps3:latest
FROM python:3.10-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    SM_MODEL_DIR=/opt/ml/model PATH="/opt/program:${PATH}"

RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# SageMaker calls `serve`; expose /ping + /invocations on 8080 via gunicorn.
COPY inference.py serve.py /opt/program/
WORKDIR /opt/program
RUN printf '#!/bin/bash\nexec gunicorn --bind 0.0.0.0:8080 --workers 2 --timeout 120 serve:app\n' > /usr/local/bin/serve \
    && chmod +x /usr/local/bin/serve

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD curl -f http://localhost:8080/ping || exit 1
ENTRYPOINT ["serve"]
