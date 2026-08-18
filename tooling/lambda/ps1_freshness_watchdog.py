"""PS1 freshness watchdog.

Alerts on ABSENCE. Rules 2 and 3 catch failure; nothing else in the system
catches "it never ran". This does.

Two checks, because they fail differently:
  1. Is there a scored partition for TODAY?      -> the run did not happen
  2. How old is the NEWEST scored partition?     -> a multi-day stall that
     was alerted on day 1 and acknowledged, and has been silent since

Env: ARTIFACT_BUCKET, SCORED_PREFIX (default chicago/ps1/scored),
     SNS_TOPIC_ARN, MAX_STALE_DAYS (default 1)
"""
import os, datetime, boto3

s3 = boto3.client("s3")
sns = boto3.client("sns")


def lambda_handler(event, context):
    bucket = os.environ["ARTIFACT_BUCKET"]
    prefix = os.environ.get("SCORED_PREFIX", "chicago/ps1/scored").strip("/")
    topic = os.environ["SNS_TOPIC_ARN"]
    max_stale = int(os.environ.get("MAX_STALE_DAYS", "1"))

    today = datetime.date.today()
    alerts = []

    # list the asof= partitions
    days = []
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=f"{prefix}/", Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            part = cp["Prefix"].rstrip("/").split("/")[-1]
            if part.startswith("asof="):
                days.append(part[5:])
    days.sort()

    if str(today) not in days:
        alerts.append(
            f"NO PS1 SCORES FOR {today}. s3://{bucket}/{prefix}/asof={today}/ "
            f"does not exist. The daily scoring run did not happen -- note that "
            f"this produces NO failure event anywhere else, because nothing "
            f"started and therefore nothing failed."
        )

    if not days:
        alerts.append(
            f"s3://{bucket}/{prefix}/ has NO asof= partitions at all. "
            f"Either the prefix is wrong or PS1 scoring has never run."
        )
    else:
        newest = days[-1]
        try:
            age = (today - datetime.date.fromisoformat(newest)).days
            if age > max_stale:
                alerts.append(
                    f"PS1 SCORES ARE {age} DAYS STALE. Newest partition is "
                    f"asof={newest}. Anything downstream reading these is "
                    f"serving {age}-day-old predictions as though current."
                )
        except ValueError:
            alerts.append(f"Newest partition name is not a date: asof={newest}")

    if alerts:
        body = "PS1 freshness watchdog\n\n" + "\n\n".join(alerts)
        body += f"\n\nbucket={bucket} prefix={prefix} partitions_found={len(days)}"
        sns.publish(TopicArn=topic, Subject="PS1 scores missing or stale", Message=body)
        return {"ok": False, "alerts": alerts}

    return {"ok": True, "newest": days[-1], "partitions": len(days)}
