# =============================================================================
# PS5 RUL scorer -- BYOC image for SageMaker Batch Transform (params-only numpy).
# Slim: no scikit-survival / sklearn at serve time. ~150-200 MB.
#
# Build + push to ECR (us-east-1, dev acct 170202974600):
#   ACCT=170202974600 REGION=us-east-1 REPO=cubic-mars-ps5-scorer TAG=v5.1
#   aws ecr create-repository --repository-name $REPO --region $REGION 2>/dev/null || true
#   aws ecr get-login-password --region $REGION | docker login --username AWS \
#     --password-stdin $ACCT.dkr.ecr.$REGION.amazonaws.com
#   docker build -t $REPO:$TAG .
#   docker tag $REPO:$TAG $ACCT.dkr.ecr.$REGION.amazonaws.com/$REPO:$TAG
#   docker push $ACCT.dkr.ecr.$REGION.amazonaws.com/$REPO:$TAG
#   # -> image_uri = 170202974600.dkr.ecr.us-east-1.amazonaws.com/cubic-mars-ps5-scorer:v5.1
# =============================================================================
FROM python:3.11-slim

# SageMaker inference contract: respond on :8080 to /ping and /invocations.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    SM_MODEL_DIR=/opt/ml/model

WORKDIR /opt/program

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ps5_scoring_core.py serve.py ./
COPY serve /usr/local/bin/serve
RUN chmod +x /usr/local/bin/serve

# /opt/ml/model is populated by SageMaker from model.tar.gz at run time.
EXPOSE 8080
# SageMaker invokes `docker run <image> serve`; `serve` (on PATH) launches gunicorn.
# No ENTRYPOINT so the `serve` arg resolves directly to /usr/local/bin/serve.
CMD ["serve"]
