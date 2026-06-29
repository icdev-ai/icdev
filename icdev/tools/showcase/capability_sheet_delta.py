#!/usr/bin/env python3
"""Detect new feat: commits since last run and draft new capability sheet rows via LLM."""
import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def _get_head_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
    ).strip()


def _get_new_feat_commits(since_sha: str) -> list[dict]:
    try:
        raw = subprocess.check_output(
            ["git", "log", f"{since_sha}..HEAD", "--format=%H|%s"],
            cwd=ROOT, text=True
        ).strip()
    except subprocess.CalledProcessError:
        return []
    if not raw:
        return []
    commits = []
    for line in raw.splitlines():
        if "|" not in line:
            continue
        sha, subject = line.split("|", 1)
        if subject.startswith("feat"):
            area = re.split(r"[:(]", subject.lstrip("feat").lstrip("("), 1)[0].strip(" :(")
            commits.append({"sha": sha, "subject": subject, "area": area})
    return commits


def _group_by_area(commits: list[dict]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for c in commits:
        groups.setdefault(c["area"], []).append(c["subject"])
    return groups


def _call_llm(commits: list[dict], existing: dict) -> dict | None:
    try:
        from icdev.tools.llm.router import LLMRouter
        from icdev.tools.llm.router import LLMRequest
    except ImportError:
        return None

    grouped = _group_by_area(commits)
    commit_summary = "\n".join(
        f"  [{area}]: " + "; ".join(subjects)
        for area, subjects in grouped.items()
    )

    existing_challenge_ids = [c["id"] for c in existing.get("challenges", [])]
    existing_cap_ids       = [c["id"] for c in existing.get("capabilities", [])]
    existing_canvas_ids    = [c["id"] for c in existing.get("canvases", [])]

    prompt = f"""You are updating the ICDEV™ Capability Sheet spreadsheet.

New feature commits since the last update:
{commit_summary}

Current highest IDs:
  challenges:   {max(existing_challenge_ids, default=0)}
  capabilities: {max(existing_cap_ids, default=0)}
  canvases:     {max(existing_canvas_ids, default=0)}

Based ONLY on the commits listed above, return a JSON object with these keys:
  new_challenges:      list of new challenge rows (all fields: id, domain, challenge,
                       business_impact, solution, modules, compliance, outcome,
                       so_what, status="draft")
  updated_challenges:  list of {{id, field, new_value}} patches to existing rows
  new_capabilities:    list of new capability rows (all fields: id, capability,
                       what_it_does, modules, compliance, solves, so_what,
                       status="draft")
  updated_capabilities: list of {{id, field, new_value}} patches to existing rows
  new_canvases:        list of new canvas rows (all fields: id, canvas, route,
                       use_case, challenge, how_it_solves, status="draft")

Rules:
- Only generate rows if the commits clearly introduce a new user-facing capability.
- Assign IDs sequentially starting after the current highest.
- set status="draft" on all new rows.
- Return ONLY valid JSON, no markdown fences.
"""

    try:
        router = LLMRouter()
        result = router.invoke("capability_sheet_delta", LLMRequest(prompt=prompt, max_tokens=4000))
        text = getattr(result, "text", None) or getattr(result, "content", "") or str(result)
        # strip markdown fences
        text = re.sub(r"^```[a-z]*\n?", "", text.strip())
        text = re.sub(r"\n?```$", "", text.strip())
        return json.loads(text)
    except Exception as exc:
        print(f"[delta] LLM call failed: {exc}", file=sys.stderr)
        return None


def _apply_patches(data: dict, llm_result: dict) -> dict:
    ch_map  = {c["id"]: c for c in data.get("challenges", [])}
    cap_map = {c["id"]: c for c in data.get("capabilities", [])}

    for patch in llm_result.get("updated_challenges", []):
        if patch["id"] in ch_map:
            ch_map[patch["id"]][patch["field"]] = patch["new_value"]

    for patch in llm_result.get("updated_capabilities", []):
        if patch["id"] in cap_map:
            cap_map[patch["id"]][patch["field"]] = patch["new_value"]

    max_ch_id  = max((c["id"] for c in data.get("challenges", [])),  default=0)
    max_cap_id = max((c["id"] for c in data.get("capabilities", [])), default=0)
    max_cv_id  = max((c["id"] for c in data.get("canvases", [])),     default=0)

    for new_ch in llm_result.get("new_challenges", []):
        max_ch_id += 1
        new_ch["id"] = max_ch_id
        new_ch["status"] = "draft"
        data["challenges"].append(new_ch)

    for new_cap in llm_result.get("new_capabilities", []):
        max_cap_id += 1
        new_cap["id"] = max_cap_id
        new_cap["status"] = "draft"
        data["capabilities"].append(new_cap)

    for new_cv in llm_result.get("new_canvases", []):
        max_cv_id += 1
        new_cv["id"] = max_cv_id
        new_cv["status"] = "draft"
        data["canvases"].append(new_cv)

    return data


def main():
    parser = argparse.ArgumentParser(description="Delta-detect new feat commits and draft capability sheet rows")
    parser.add_argument("--yaml",    default=str(ROOT / "args" / "capability_sheet.yaml"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    yaml_path = Path(args.yaml)
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    last_sha = data.get("meta", {}).get("last_commit_sha", "")
    head_sha = _get_head_sha()

    if last_sha == head_sha:
        print("[delta] No new commits since last run.")
        return

    commits = _get_new_feat_commits(last_sha)
    feat_commits = [c for c in commits if "feat" in c["subject"]]
    print(f"[delta] {len(feat_commits)} feat: commits since {last_sha[:8]}")

    n_new_ch = n_new_cap = n_new_cv = n_patches = 0

    if feat_commits:
        llm_result = _call_llm(feat_commits, data)
        if llm_result is None:
            print("[delta] LLM unavailable - updating SHA only, no rows drafted.")
        else:
            n_new_ch  = len(llm_result.get("new_challenges", []))
            n_new_cap = len(llm_result.get("new_capabilities", []))
            n_new_cv  = len(llm_result.get("new_canvases", []))
            n_patches = (len(llm_result.get("updated_challenges", [])) +
                         len(llm_result.get("updated_capabilities", [])))

            if args.dry_run:
                print(f"[delta] DRY RUN: would add {n_new_ch} challenges, "
                      f"{n_new_cap} capabilities, {n_new_cv} canvases; "
                      f"{n_patches} patches")
                print(f"[delta] Would update last_commit_sha to {head_sha[:8]}")
                return
            data = _apply_patches(data, llm_result)
    else:
        print("[delta] No feat: commits found - updating SHA only.")

    if not args.dry_run:
        data.setdefault("meta", {})["last_commit_sha"] = head_sha
        data["meta"]["last_updated"] = str(date.today())
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, sort_keys=False, default_flow_style=False)

    print(f"[delta] {n_new_ch} new challenges, {n_patches} patches, "
          f"{n_new_cap} new capabilities, {n_new_cv} new canvases "
          f"-- status=draft; review {yaml_path} then run generator")


if __name__ == "__main__":
    main()
