FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /opt/ml/code/requirements.txt
RUN pip install --no-cache-dir flask -r /opt/ml/code/requirements.txt

COPY inference_ps1.py /opt/ml/code/inference.py

RUN mkdir -p /opt/ml/model /opt/ml/input /opt/ml/output \
    && printf '#!/bin/bash\npython /opt/ml/code/inference.py\n' \
       > /usr/local/bin/serve \
    && chmod +x /usr/local/bin/serve

ENV PYTHONUNBUFFERED=TRUE

CMD ["serve"]
