# Processing-job image for the PS2 serial-grain notebook
# (notebooks/ps2_cascading_failure/PS2_Serial_Grain_Analysis_v1_FIXED.ipynb).
#
# WHY THIS EXISTS.
# PS2 has TWO producers writing to the same chicago/ps2_outputs prefix. The
# patterns notebook is Spark-native (1,061 pyspark references) and runs as a
# Databricks task in medallion_ps2_daily. This one has ZERO pyspark references,
# 8 sagemaker imports and a SageMaker MLflow ARN -- it is a pandas workload and
# a Spark job cluster is the wrong place for it. With no schedule of its own it
# fell five weeks behind its sibling, and both families publish onto one tab
# with nothing on screen saying which was which.
#
# NO SPARK, DELIBERATELY. The PS3 image bakes JRE 17, pyspark and matched S3A
# jars because its notebook builds a local[*] session. This one reads parquet
# through pandas + s3fs and writes with boto3, so none of that belongs here.
#
# Base image comes from the ECR PUBLIC mirror, not Docker Hub: an anonymous
# Hub pull out of CodeBuild is rate-limited by source IP and killed two
# dashboard builds on 21-Sep with a 429 before npm even ran.
#
# Build via CodeBuild (CloudShell has no docker daemon, Docker Desktop is
# unusable here) using the buildspec beside this file, which BOOTS the image
# and only pushes if it proves it can do what the notebook needs. Push to
# cubic-mars-ps2-processing -- never a cubic-pdm/* repo, which are
# endpoint-serving images with a /ping + /invocations contract this has not.
FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# libgomp1: OpenMP runtime for scikit-learn's and hmmlearn's compiled paths.
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# The build-time gate notebook. Baked in so the buildspec can papermill it
# through this exact image before the image is pushed -- same kernel, same
# papermill, same flags as the ENTRYPOINT below. It costs ~4 KB and it is the
# difference between "the image built" and "the image runs a notebook".
COPY gate_notebook.ipynb /opt/gate/gate_notebook.ipynb

# The notebook picks a scratch directory by probing a candidate list that
# starts with /home/sagemaker-user -- a Studio path that does not exist here.
# Create it so the probe resolves to a real writable directory rather than
# falling through to whatever /tmp happens to offer.
RUN mkdir -p /home/sagemaker-user
WORKDIR /home/sagemaker-user

# SageMaker Processing mounts the ProcessingInput under
# /opt/ml/processing/input and collects /opt/ml/processing/output at job end.
# papermill executes the notebook and writes the executed copy to the output
# mount -- that copy is the RUN RECORD, not the data. The real outputs are the
# notebook's own 27 boto3 writes to chicago/ps2_outputs/, which is exactly
# where cubic-mars-ps2-rds-loader already looks.
ENTRYPOINT ["papermill", \
    "/opt/ml/processing/input/notebook/PS2_Serial_Grain_Analysis_v1_FIXED.ipynb", \
    "/opt/ml/processing/output/PS2_Serial_Grain_Analysis_v1_FIXED.executed.ipynb", \
    "--kernel", "python3", "--log-output", "--request-save-on-cell-execute"]
