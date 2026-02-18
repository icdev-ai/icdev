# Create Pull Request / Merge Request

Based on the `Instructions` below, take the `Variables` follow the `Run` section to create a pull request (GitHub) or merge request (GitLab). Then follow the `Report` section to report the results of your work.

## Variables

branch_name: $1
issue: $2
plan_file: $3
run_id: $4

## Instructions

- Generate a PR/MR title in the format: `<issue_type>: #<issue_number> - <issue_title>`
- The PR/MR body should include:
  - A summary section with the issue context
  - Link to the implementation `plan_file` if it exists
  - Reference to the issue (Closes #<issue_number>)
  - ICDEV tracking run ID
  - CUI marking: `CUI // SP-CTI`
  - A checklist of what was done
- Extract issue number, type, and title from the issue JSON
- Examples of PR titles:
  - `feat: #123 - Add user authentication`
  - `bug: #456 - Fix login validation error`
  - `chore: #789 - Update dependencies`
- Works with both GitHub (`gh`) and GitLab (`glab`)

## Run

1. Run `git diff origin/main...HEAD --stat` to see changed files
2. Run `git log origin/main..HEAD --oneline` to see commits
3. Run `git push -u origin <branch_name>` to push the branch
4. Detect platform:
   - If `gh` is available and remote is GitHub: use `gh pr create --title "<title>" --body "<body>" --base main`
   - If `glab` is available and remote is GitLab: use `glab mr create --title "<title>" --description "<body>" --target-branch main --yes`
5. Capture the PR/MR URL from the output

## Report

Return ONLY the PR/MR URL that was created (no other text)
