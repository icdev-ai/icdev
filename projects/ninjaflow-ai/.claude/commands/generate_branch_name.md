# Generate Git Branch Name

Based on the `Instructions` below, take the `Variables` follow the `Run` section to generate a concise Git branch name following the specified format. Then follow the `Report` section to report the results of your work.

## Variables

issue_class: $1
run_id: $2
issue: $3

## Instructions

- Generate a branch name in the format: `<issue_class>-issue-<issue_number>-icdev-<run_id>-<concise_name>`
- The `<concise_name>` should be:
  - 3-6 words maximum
  - All lowercase
  - Words separated by hyphens
  - Descriptive of the main task/feature
  - No special characters except hyphens
- Examples:
  - `feat-issue-123-icdev-a1b2c3d4-add-user-auth`
  - `bug-issue-456-icdev-e5f6g7h8-fix-login-error`
  - `chore-issue-789-icdev-i9j0k1l2-update-dependencies`
- Extract the issue number, title, and body from the issue JSON
- Works with both GitHub and GitLab issue formats

## Run

Run `git checkout main` to switch to the main branch
Run `git pull` to pull the latest changes from the main branch
Run `git checkout -b <branch_name>` to create and switch to the new branch

## Report

After generating the branch name:
Return ONLY the branch name that was created (no other text)
