# CUI // SP-CTI
# Migrate Elasticsearch index templates and aliases to OpenSearch.
import requests
from requests_aws4auth import AWS4Auth
import boto3

ES_HOST = "https://elasticsearch.kube-system.svc.cluster.local:9200"
OS_HOST = "https://search-analytics-xxxx.us-east-1.es.amazonaws.com"
REGION  = "us-east-1"

creds = boto3.Session().get_credentials()
awsauth = AWS4Auth(creds.access_key, creds.secret_key, REGION, "es",
                   session_token=creds.token)

# Export templates from ES
templates = requests.get(f"{ES_HOST}/_template", verify=False).json()
for name, body in templates.items():
    if name.startswith("."):
        continue
    r = requests.put(f"{OS_HOST}/_template/{name}",
                     auth=awsauth, json=body)
    print(f"Template {name}: {r.status_code}")

# Export and recreate aliases
aliases = requests.get(f"{ES_HOST}/_cat/aliases?format=json",
                       verify=False).json()
for alias in aliases:
    body = {alias["alias"]: {"is_write_index": alias.get("is_write_index") == "true"}}
    r = requests.post(f"{OS_HOST}/_aliases",
                      auth=awsauth,
                      json={"actions": [{"add": {"index": alias["index"], "alias": alias["alias"]}}]})
    print(f"Alias {alias['alias']}: {r.status_code}")
