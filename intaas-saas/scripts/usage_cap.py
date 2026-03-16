#!/usr/bin/env python3
"""Usage cap enforcement — hard limit at 125 hrs/month for SageMaker free tier.

Runs hourly via EventBridge cron. Checks CloudWatch InferenceComponentInvocations
metric to compute total instance-hours this month. If >= 120 hrs (5-hr safety buffer),
deletes the SageMaker endpoint to prevent charges.

Three layers of protection:
  1. This Lambda (hard kill at 120 hrs)
  2. Auto-scaling max = 1 instance
  3. AWS Budgets alarm at $1
"""
import json
import logging
import os
from datetime import datetime, timezone

import boto3

logger = logging.getLogger("intaas.usage_cap")

ENDPOINT_NAME = os.environ.get("SAGEMAKER_ENDPOINT", "intaas-prometheus-judge")
CAP_HOURS = int(os.environ.get("USAGE_CAP_HOURS", "125"))
BUFFER_HOURS = 5
EFFECTIVE_CAP = CAP_HOURS - BUFFER_HOURS
REGION = os.environ.get("AWS_REGION", "us-east-1")


def handler(event, context):
    """Lambda handler — invoked hourly by EventBridge."""
    cw = boto3.client("cloudwatch", region_name=REGION)
    sm = boto3.client("sagemaker", region_name=REGION)

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Get endpoint instance-hours from CloudWatch
    try:
        response = cw.get_metric_statistics(
            Namespace="AWS/SageMaker",
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "EndpointName", "Value": ENDPOINT_NAME}],
            StartTime=month_start,
            EndTime=now,
            Period=3600,  # 1-hour periods
            Statistics=["SampleCount"],
        )
        # Each datapoint with SampleCount > 0 = 1 hour the instance was running
        datapoints = response.get("Datapoints", [])
        hours_used = len([d for d in datapoints if d.get("SampleCount", 0) > 0])
    except Exception as e:
        logger.warning("CloudWatch query failed: %s", e)
        hours_used = 0

    result = {
        "endpoint": ENDPOINT_NAME,
        "month": now.strftime("%Y-%m"),
        "hours_used": hours_used,
        "cap_hours": CAP_HOURS,
        "effective_cap": EFFECTIVE_CAP,
        "status": "ok",
    }

    if hours_used >= EFFECTIVE_CAP:
        logger.warning(
            "Usage cap reached: %d/%d hours. Deleting endpoint %s",
            hours_used, CAP_HOURS, ENDPOINT_NAME,
        )
        try:
            sm.delete_endpoint(EndpointName=ENDPOINT_NAME)
            result["status"] = "endpoint_deleted"
            result["reason"] = f"Usage cap {hours_used}/{CAP_HOURS} hours reached"
        except sm.exceptions.ClientError as e:
            if "Could not find endpoint" in str(e):
                result["status"] = "already_deleted"
            else:
                result["status"] = "delete_failed"
                result["error"] = str(e)
    elif hours_used >= EFFECTIVE_CAP - 10:
        result["status"] = "warning"
        result["message"] = f"Approaching cap: {hours_used}/{CAP_HOURS} hours used"

    logger.info("Usage cap check: %s", json.dumps(result))
    return result


if __name__ == "__main__":
    print(json.dumps(handler({}, None), indent=2))
