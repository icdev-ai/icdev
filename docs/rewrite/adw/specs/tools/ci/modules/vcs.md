# Spec: `tools/ci/modules/vcs.py`

_OPT-75 Phase 1 clean-room spec._

## Purpose

Unified Version Control System abstraction for GitHub and GitLab. Auto-
detects the platform from `git remote origin`'s URL, then exposes a
single API for issues, comments, and pull/merge requests using the
underlying CLI (`gh` for GitHub, `glab` for GitLab).

## Class

### `VCS`

#### Class constants
* `PLATFORM_GITHUB = "github"`
* `PLATFORM_GITLAB = "gitlab"`

#### `__init__(self, platform: str | None = None, repo_path: str | None = None)`
Auto-detects when both args are `None`. If `platform` is supplied, it
overrides detection; if `repo_path` is supplied, it overrides whatever
the detector parsed.

#### Detection (`_detect_platform`)
Run `git remote get-url origin`. Parse the URL with two regexes:
- SSH: `git@<host>:<path>(.git)?`
- HTTPS: `https?://<host>/<path>(.git)?`

If neither matches, raise `ValueError`. Host containing `github` (case
insensitive) → GitHub. Anything else → GitLab.

If the remote command fails (non-zero rc), raise `ValueError` with the
underlying stderr.

#### Properties
* `cli -> str`: `"gh"` when GitHub, `"glab"` when GitLab.
* `is_github -> bool`
* `is_gitlab -> bool`

#### Issues
* `fetch_issue(issue_number: int) -> dict`
  GitHub: `gh issue view <n> -R <repo> --json number,title,body,state,
  author,labels,comments,createdAt,updatedAt,url`. GitLab:
  `glab issue view <n> --output json`. Raises `RuntimeError` on
  non-zero rc.

* `list_open_issues(limit: int = 50) -> list[dict]`
  GitHub: `gh issue list --repo <repo> --state open --json number,title,
  body,labels,createdAt,updatedAt --limit <n>`. GitLab:
  `glab issue list --opened --output json --per-page <n>`. Returns `[]`
  on failure or empty stdout.

* `comment_on_issue(issue_number: int, body: str) -> bool`
  GitHub: `gh issue comment <n> -R <repo> --body <body>`. GitLab:
  `glab issue note <n> --message <body>`. Returns True on rc == 0.

* `fetch_issue_comments(issue_number: int) -> list[dict]`
  GitHub: `gh issue view <n> --repo <repo> --json comments`, then
  return the `comments` key. GitLab: `glab api projects/:id/issues/<n>
  /notes --paginate`. Returns `[]` on failure.

#### PR / MR
* `create_pr(title: str, body: str, base: str = "main", head: str | None = None) -> str | None`
  GitHub: `gh pr create --repo <repo> --title <t> --body <b> --base <base> [--head <head>]`.
  GitLab: `glab mr create --title <t> --description <b> --target-branch
  <base> --remove-source-branch --yes [--source-branch <head>]`.
  60 second timeout for both.
  On success, scan the combined stdout/stderr for the first
  `https?://...` URL and return it; if no URL is found, return the
  stripped stdout (or the literal `"created"`).
  On failure, return `None`.

* `check_pr_exists(branch_name: str) -> str | None`
  GitHub: `gh pr list --repo <repo> --head <branch> --json url`. Return
  `prs[0].url` if any.
  GitLab: `glab mr list --source-branch <branch> --output json`. Return
  `mrs[0].web_url` (or `url` if `web_url` missing).
  Return `None` if no result.

* `comment_on_pr(pr_number: int, body: str) -> bool`
  GitHub: `gh pr comment <n> -R <repo> --body <body>`. GitLab:
  `glab mr note <n> --message <body>`.

#### Utility
* `get_remote_url() -> str` — `git remote get-url origin` (stripped).
* `whoami() -> str` — `gh auth status` or `glab auth status` stdout.
* `__repr__()` includes platform + repo path.

## Internal helpers

### `_get_env() -> dict`
Inherit `os.environ` and overlay tokens:
* `GH_TOKEN` ← `GITHUB_PAT` or `GH_TOKEN`
* `GITLAB_TOKEN` ← `GITLAB_TOKEN` or `GLAB_TOKEN`
* `GITLAB_URL` if set

### `_run(cmd: list[str], cwd: str | None = None, timeout: int = 30) -> tuple[str, str, int]`
Run a CLI subprocess with `_get_env()`, `text=True`, `capture_output=True`,
`cwd` defaulting to `PROJECT_ROOT`. Returns trimmed
`(stdout, stderr, returncode)`.

## Forbidden

* No DB writes.
* No `print()` for production output.
* `git push --force` and friends are blocked at the outer hook layer
  (`tools/airgap/hook_compat.py`); never reintroduce them here.
