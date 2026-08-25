# Processing-job image for the PS5 reliability/survival notebook
# (notebooks/ps5_reliability_survival/PS5_Reliability_Survival_v5_6.ipynb).
#
# The stack is PRE-BAKED so the notebook's Cell 1 finds every pinned package
# importable, installs nothing and prints no restart banner - that is what
# makes Run All (and therefore papermill) work end to end in one kernel.
#
# Build via SageMaker Studio Image Build / sm-docker / CodeBuild (CloudShell
# has no docker daemon) - same recipe as the PS3 image: zip this folder to the
# artifacts bucket and start-build on a privileged amazonlinux standard:5.0
# CodeBuild project. Push to a NEW repository, cubic-mars-ps5-processing:
#   sm-docker build . --repository cubic-mars-ps5-processing:v5.6
# Do NOT reuse the cubic-pdm/* repos - those are endpoint-serving images with
# a /ping + /invocations contract; this is a batch Processing image with none.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# libgomp1: OpenMP runtime needed by scikit-survival's compiled estimators.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# SageMaker Processing mounts the ProcessingInput (the notebook) under
# /opt/ml/processing/input and collects /opt/ml/processing/output at job end.
# papermill executes the notebook and saves the executed copy to the output
# mount - that copy is the run record. The engine's REAL outputs are the
# notebook's own S3 writes in Cell 7 (chicago/ps5/notebook_outputs/), gated by
# PS5_PUBLISH in the job environment, not a ProcessingOutput.
ENTRYPOINT ["papermill", \
    "/opt/ml/processing/input/notebook/PS5_Reliability_Survival_v5_6.ipynb", \
    "/opt/ml/processing/output/PS5_Reliability_Survival_v5_6.executed.ipynb", \
    "--kernel", "python3", "--log-output", "--request-save-on-cell-execute"]
