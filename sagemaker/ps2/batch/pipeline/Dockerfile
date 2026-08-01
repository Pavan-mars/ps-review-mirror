# =============================================================================
# PS2 pipeline ORCHESTRATION image -- runs refresh_device_state.py,
# run_batch_transform.py, and load_transform_output_to_rds.py as SageMaker
# Processing Jobs, one per Step Function state. NOT the serving image (that's
# ../container/Dockerfile, kept deliberately slim) -- this one carries the
# heavier libs (mlxtend, networkx, hmmlearn, sagemaker SDK, psycopg2) needed to
# fit/refresh/orchestrate, matching the "notebook does the heavy lifting,
# container just serves" split used throughout this project.
#
# IMPORTANT -- build context is sagemaker/ps2/batch/ (the PARENT of both
# pipeline/ and container/), NOT sagemaker/ps2/batch/pipeline/. Docker's COPY
# cannot reach outside the build context, and this image needs
# container/ps2_scoring_core.py (the shared scoring core) alongside the
# pipeline/ scripts -- so the context has to be the common parent of both.
#
# Build + push to ECR (us-east-1, dev acct 170202974600):
#   ACCT=170202974600 REGION=us-east-1 REPO=cubic-mars-ps2-pipeline TAG=v1.0
#   cd sagemaker/ps2/batch   # <-- build context is here, NOT inside pipeline/
#   aws ecr create-repository --repository-name $REPO --region $REGION 2>/dev/null || true
#   aws ecr get-login-password --region $REGION | docker login --username AWS \
#     --password-stdin $ACCT.dkr.ecr.$REGION.amazonaws.com
#   docker build -f pipeline/Dockerfile -t $REPO:$TAG .
#   docker tag $REPO:$TAG $ACCT.dkr.ecr.$REGION.amazonaws.com/$REPO:$TAG
#   docker push $ACCT.dkr.ecr.$REGION.amazonaws.com/$REPO:$TAG
# =============================================================================
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1

WORKDIR /opt/ml/processing/pipeline

COPY pipeline/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pipeline/fit_ps2_models.py pipeline/package_model.py pipeline/register_mlflow.py \
     pipeline/run_batch_transform.py pipeline/refresh_device_state.py pipeline/load_transform_output_to_rds.py ./
COPY container/ps2_scoring_core.py ./ps2_scoring_core.py

# SageMaker Processing invokes this image with an explicit ContainerEntrypoint
# per job (see ../state_machine.asl.json) -- no fixed CMD/ENTRYPOINT needed here.
