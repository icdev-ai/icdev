# CUI // SP-CTI
# Elasticsearch → OpenSearch: S3 snapshot + restore migration
import boto3
import time
import requests
from requests_aws4auth import AWS4Auth

ES_HOST   = "https://elasticsearch.kube-system.svc.cluster.local:9200"
OS_HOST   = "https://search-analytics-xxxx.us-east-1.es.amazonaws.com"
S3_BUCKET = "es-migration-snapshots-ACCOUNTID"
REPO_NAME = "migration-repo"
SNAPSHOT  = "pre-migration-snapshot"
REGION    = "us-east-1"

session = boto3.Session()
creds = session.get_credentials()
awsauth = AWS4Auth(creds.access_key, creds.secret_key, REGION, "es",
                   session_token=creds.token)

def register_repo(host, direction):
    """Register S3 snapshot repository on source (ES) or target (OpenSearch)."""
    body = {"type": "s3", "settings": {
        "bucket": S3_BUCKET, "region": REGION,
        "role_arn": "arn:aws:iam::ACCOUNT_ID:role/ESSnapshotRole",
    }}
    r = requests.put(f"{host}/_snapshot/{REPO_NAME}", auth=awsauth,
                     json=body, verify=False)
    print(direction, r.status_code, r.text)

def take_snapshot():
    r = requests.put(f"{ES_HOST}/_snapshot/{REPO_NAME}/{SNAPSHOT}",
                     auth=awsauth, json={"indices": "_all"}, verify=False)
    print("Snapshot started:", r.status_code)
    # Poll until complete
    for _ in range(60):
        s = requests.get(f"{ES_HOST}/_snapshot/{REPO_NAME}/{SNAPSHOT}",
                         auth=awsauth, verify=False).json()
        state = s["snapshots"][0]["state"]
        if state == "SUCCESS":
            return
        if state in ("FAILED", "PARTIAL"):
            raise RuntimeError(f"Snapshot failed: {state}")
        time.sleep(10)

def restore_snapshot():
    r = requests.post(
        f"{OS_HOST}/_snapshot/{REPO_NAME}/{SNAPSHOT}/_restore",
        auth=awsauth,
        json={"indices": "_all", "rename_pattern": "(.+)",
              "rename_replacement": "$1", "include_global_state": False},
    )
    print("Restore:", r.status_code, r.text)

if __name__ == "__main__":
    register_repo(ES_HOST, "ES source")
    take_snapshot()
    register_repo(OS_HOST, "OpenSearch target")
    restore_snapshot()
    print("Done — validate index counts before updating application endpoints.")
