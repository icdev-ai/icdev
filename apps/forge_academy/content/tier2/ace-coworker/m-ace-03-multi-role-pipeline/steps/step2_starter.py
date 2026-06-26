import time
import requests

BASE_URL = "http://localhost:5050"

PIPELINE_REQUEST = {
    "pipeline": [
        # TODO: fill in your 3-stage pipeline here
        # {"role": "...", "task": "...", "hitl_required": False},
    ],
    "sequential": True,
}


def run_pipeline():
    # TODO: POST to /api/ace/pipeline
    # TODO: Poll /api/ace/pipeline/{id}/status until done or pending_hitl
    # TODO: Print each stage status + final artifact
    pass


if __name__ == "__main__":
    run_pipeline()
