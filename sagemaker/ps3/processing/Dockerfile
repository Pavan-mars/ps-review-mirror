# Processing-job image for the PS3 V26 production notebook
# (notebooks/ps3_root_cause_analysis/PS3_V26_PRODUCTION.ipynb).
#
# Why a Processing job and not a Databricks task: the notebook builds its OWN
# SparkSession with .master("local[*]") and finds its own JAVA_HOME. It is a
# standalone/SageMaker-edition notebook, it has no %pip cells and no widgets,
# and it is therefore Run-All safe as written - which is what makes papermill
# work end to end in one kernel.
#
# Unlike the PS5 image this one needs a JVM and the Hadoop S3A connector,
# because the notebook reads the silver exports over s3a://.
#
# The connector jars are BAKED IN rather than resolved at run time. The
# notebook sets spark.jars.packages=org.apache.hadoop:hadoop-aws:3.3.4, which
# makes Spark run an Ivy resolve against Maven Central on every session start -
# an external dependency at 07:00 that nothing here controls. With the jars
# already in $SPARK_HOME/jars this image sets PS3_SPARK_JARS_PACKAGES="" so the
# notebook skips the resolve and the session starts offline.
#
# The three versions below are locked TOGETHER and must move together: pyspark
# 3.5.x ships a Hadoop 3.3.4 client, hadoop-aws must match that exactly, and
# hadoop-aws 3.3.4 was built against aws-java-sdk-bundle 1.12.262. A mismatch
# surfaces as a NoSuchMethodError deep inside S3AFileSystem at first read, not
# as anything that looks like a version problem.
#
# Build via CodeBuild - CloudShell has no docker daemon and Docker Desktop is
# unusable on the workstation:
#   zip -r ps3-processing-src.zip . && aws s3 cp ps3-processing-src.zip \
#     s3://cubic-mars-pm-s3-datalake-dev-artifacts-170202974600/codebuild/
#   aws codebuild start-build --project-name <privileged amazonlinux 5.0 project>
# Push to a NEW repository, cubic-mars-ps3-processing. Do NOT reuse
# cubic-pdm/mars-ps3 - that is the endpoint-serving image, which carries a
# /ping + /invocations contract this batch image does not have.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# procps: Spark's scripts call `ps`. libgomp1: OpenMP runtime for sklearn/shap.
RUN apt-get update && apt-get install -y --no-install-recommends \
        procps curl ca-certificates libgomp1 openjdk-17-jre-headless \
    && rm -rf /var/lib/apt/lists/*

ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

ENV SPARK_HOME=/usr/local/lib/python3.11/site-packages/pyspark
ARG HADOOP_AWS=3.3.4
ARG AWS_SDK_BUNDLE=1.12.262
RUN set -eux; \
    M=https://repo1.maven.org/maven2; \
    curl -fsSL -o "$SPARK_HOME/jars/hadoop-aws-${HADOOP_AWS}.jar" \
      "$M/org/apache/hadoop/hadoop-aws/${HADOOP_AWS}/hadoop-aws-${HADOOP_AWS}.jar"; \
    curl -fsSL -o "$SPARK_HOME/jars/aws-java-sdk-bundle-${AWS_SDK_BUNDLE}.jar" \
      "$M/com/amazonaws/aws-java-sdk-bundle/${AWS_SDK_BUNDLE}/aws-java-sdk-bundle-${AWS_SDK_BUNDLE}.jar"; \
    ls -l "$SPARK_HOME/jars/" | grep -E 'hadoop-aws|aws-java-sdk'

# Empty => the notebook skips its Ivy resolve and uses the baked jars above.
# Set it to a package coordinate to restore the download behaviour.
ENV PS3_SPARK_JARS_PACKAGES=""

# SageMaker Processing mounts ProcessingInput under /opt/ml/processing/input and
# collects /opt/ml/processing/output at job end. papermill writes the executed
# copy there - that copy is the RUN RECORD. The real outputs are the notebook's
# own S3 writes to chicago/ps3_outputs/<table>/computed_date=/run_id=/, which is
# exactly the layout cubic-mars-ps3-v25-loader already reads.
ENTRYPOINT ["papermill", \
    "/opt/ml/processing/input/notebook/PS3_V26_PRODUCTION.ipynb", \
    "/opt/ml/processing/output/PS3_V26_PRODUCTION.executed.ipynb", \
    "--kernel", "python3", "--log-output", "--request-save-on-cell-execute"]
