# Auto-grader for M01 Step 1
#
# aca-vv-01: the previous version of this file defined its OWN simulate_llm_call,
# re-executed its own solution, and asserted on its own output — so it passed no
# matter what the learner submitted, including an empty file. It was step 1 of
# mission 1: the Academy's front door graded nothing.
#
# run_code concatenates the learner's script and this file into ONE program, so the
# learner's module-level names and their stdout are visible here. Grade those.

# --- 1. The learner must actually have called the simulator -------------------
_fn = globals().get("simulate_llm_call")
assert callable(_fn), (
    "simulate_llm_call is not defined — keep the starter's function and call it."
)

_resp = globals().get("response")
assert isinstance(_resp, dict), (
    "No `response` dict found. Call simulate_llm_call(system_prompt, user_message) "
    "and assign the result to `response`."
)
for _key in ("content", "model", "usage"):
    assert _key in _resp, f"The response is missing {_key!r}."

# --- 2. The call must have used the learner's own prompts ---------------------
_sys_prompt = globals().get("system_prompt", "")
_user_msg = globals().get("user_message", "")
assert isinstance(_sys_prompt, str) and _sys_prompt.strip(), "system_prompt is empty."
assert isinstance(_user_msg, str) and _user_msg.strip(), "user_message is empty."
assert _resp["content"], "The response content is empty."
assert _user_msg[:20] in _resp["content"], (
    "The response does not reflect your user_message — call the simulator with your "
    "own prompts rather than hardcoding a result."
)

# --- 3. Token usage must be read from the response, not invented --------------
_usage = _resp["usage"]
assert {"input_tokens", "output_tokens"} <= set(_usage), (
    "usage must carry input_tokens and output_tokens."
)
_expected_in = len(_sys_prompt.split()) + len(_user_msg.split())
assert _usage["input_tokens"] == _expected_in, (
    f"input_tokens should be {_expected_in} for your prompts — read it from the "
    "response instead of hardcoding a number."
)

print("PASS: you made the call, read the response, and reported real token usage.")
