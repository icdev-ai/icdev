"""Run and monitor a multi-role ACE pipeline (sandbox simulation).

The Academy sandbox has no network access, so this exercise does not call the
dashboard. You build the pipeline request and simulate how each stage would progress.
"""
import json

BASE_URL = "http://localhost:5050"

# Valid roles (the grader rejects anything else):
#   "ai_developer", "agent_developer", "security_analyst",
#   "data_engineer", "devops_engineer", "compliance_officer"

PIPELINE_REQUEST = {
    "pipeline": [
        # TODO: fill in at least 3 stages covering agent design, security review and
        #       compliance check, using roles from the list above, e.g.
        # {"role": "agent_developer", "task": "...", "hitl_required": False},
    ],
    "sequential": True,
}


def run_pipeline():
    # TODO: print the request you would submit (e.g. json.dumps(PIPELINE_REQUEST, indent=2))
    # TODO: simulate each stage in order and print "stage <n> <role>: <status>", where
    #       status is "pending_hitl" if the stage has hitl_required=True, else "done"
    # TODO: print a final line saying whether the pipeline finished or is waiting on HITL
    pass


if __name__ == "__main__":
    run_pipeline()
