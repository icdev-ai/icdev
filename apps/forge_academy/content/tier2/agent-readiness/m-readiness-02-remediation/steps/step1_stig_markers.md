---
ontology_id: icdev:mission:m-readiness-02-remediation:step:1
step_class: icdev:coding
---
# STIG Compliance Remediation

Pillar 10 (STIG Compliance) looks for STIG vulnerability ID references (`V-NNNNN` or `V-NNNNNN`) in source and config files, plus STIG references in documentation and a STIG checklist. In code the convention is a comment such as `# STIG V-XXXXXX: <requirement>` next to the security-relevant function.

## Adding STIG markers

```python
# STIG V-220133: Application must enforce session timeout
SESSION_TIMEOUT_SECONDS = 1800

# STIG V-220160: Application must not store plaintext passwords
def hash_password(plaintext: str) -> str:
    salt = os.urandom(16)  # per-password random salt, stored alongside the hash
    digest = hashlib.pbkdf2_hmac("sha256", plaintext.encode(), salt, 600_000)
    return salt.hex() + ":" + digest.hex()
```

## Your task

The starter maps six control families to a STIG comment (`STIG_MAPPINGS`) and to the function-name keywords that signal that family (`PATTERN_KEYWORDS`). The V-IDs in the starter are illustrative placeholders: in a real remediation, look each one up in the STIG that applies to your system (for application code, the Application Security and Development STIG). The checker only needs a `V-NNNNN` / `V-NNNNNN` reference to count it.

Complete `find_functions_needing_markers(filepath)` so that it:

1. Reads the Python file at `filepath` (a `pathlib.Path`)
2. Finds each function definition and its line number (`ast` or a regex on `def` lines both work)
3. Matches the lower-cased function name against `PATTERN_KEYWORDS`: a function belongs to a family when one of that family's keywords appears in its name; the first matching family wins
4. Returns a list of dicts `{"line": <int>, "name": <function name>, "stig_comment": STIG_MAPPINGS[<family>]}`; functions that match no family are left out

`inject_markers()` already prints the dry run; writing the comments into the file (`--apply`) is optional. The grader runs your function on a small sample file containing `login()` and `other()`: only `login` (line 1) should come back, with the `auth` comment.
