#!/usr/bin/env python3
# Bot command handler: respond to @bot check, @bot allow, @bot deny
# All printed messages and comments are in English.

import os
import sys
import json
import subprocess
from github import Github

# authorized moderator username (you)
MODERATOR = os.environ.get("BOT_USER", "Artyomka628")

def get_event_payload():
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not event_path or not os.path.exists(event_path):
        return {}
    with open(event_path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_pr_number_from_event(payload):
    if "issue" in payload and payload["issue"] and "number" in payload["issue"]:
        return int(payload["issue"]["number"])
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/pull/"):
        parts = ref.split("/")
        if len(parts) >= 3:
            try:
                return int(parts[2])
            except:
                pass
    return None

def find_manifest_hash_comment(comments, MARKER_START="<!-- MANIFEST_HASHES"):
    for c in reversed(comments):
        body = c.body or ""
        if MARKER_START in body:
            try:
                start = body.index(MARKER_START) + len(MARKER_START)
                end = body.index("END MANIFEST_HASHES -->", start)
                json_text = body[start:end].strip()
                data = json.loads(json_text)
                return c, data
            except Exception:
                continue
    return None, None

def compute_local_hashes(manifest_paths):
    import hashlib
    result = {}
    for p in manifest_paths:
        try:
            with open(p, "rb") as f:
                result[p] = hashlib.sha256(f.read()).hexdigest()
        except Exception:
            result[p] = None
    return result

def main():
    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        print("ERROR: GITHUB_TOKEN is not set")
        sys.exit(1)
    repo_name = os.environ.get("GITHUB_REPOSITORY")
    if not repo_name:
        print("ERROR: GITHUB_REPOSITORY missing")
        sys.exit(1)

    g = Github(github_token)
    repo = g.get_repo(repo_name)

    payload = get_event_payload()
    pr_number = get_pr_number_from_event(payload)
    if pr_number is None:
        print("No PR number found in event, exiting.")
        sys.exit(0)

    pr = repo.get_pull(pr_number)
    issue = repo.get_issue(pr_number)

    # Extract comment text and author
    comment_body = ""
    comment_user = ""
    if "comment" in payload and payload["comment"]:
        comment_body = payload["comment"].get("body", "")
        comment_user = payload["comment"].get("user", {}).get("login", "")
    else:
        # fallback
        comment_body = os.environ.get("GITHUB_EVENT_COMMENT_BODY", "")
        comment_user = os.environ.get("GITHUB_ACTOR", "")

    comment_body = comment_body.strip()

    # only act on comments that mention @bot
    if "@bot" not in comment_body:
        print("Comment does not mention @bot; nothing to do.")
        sys.exit(0)

    # handle @bot check: re-run validation script
    if "@bot check" in comment_body:
        issue.create_comment(f"🔁 Running manifest validation as requested by @{comment_user}...")
        # run validate_manifest.py
        res = subprocess.run(["python", ".github/scripts/validate_manifest.py"], env=os.environ)
        if res.returncode == 0:
            issue.create_comment("✅ Validation completed.")
        else:
            issue.create_comment("❌ Validation completed with errors. See above comments.")
        sys.exit(0)

    # handle allow/deny: only moderator can run these
    if "@bot allow" in comment_body or "@bot deny" in comment_body:
        if comment_user != MODERATOR:
            issue.create_comment(f"❌ Only @{MODERATOR} can approve or deny PRs.")
            sys.exit(0)

        labels = [l.name for l in pr.get_labels()]
        if "On review" not in labels:
            issue.create_comment("❌ This PR is not marked 'On review'. Approval is not allowed. Please ensure the manifest is validated and locked before approving.")
            sys.exit(0)

        # get stored hashes to ensure manifests haven't changed
        comments = list(issue.get_comments())
        marker_comment, stored_hashes = find_manifest_hash_comment(comments)
        if stored_hashes is None:
            issue.create_comment("❌ No stored manifest hashes found. The PR must be validated with `@bot check` first.")
            sys.exit(0)

        # compute current hashes for changed manifest files in PR
        files = list(pr.get_files())
        manifest_paths = [f.filename for f in files if f.filename.startswith("manifests/") and f.filename.endswith(".json")]
        local_hashes = compute_local_hashes(manifest_paths)

        # compare stored vs current
        mismatch = False
        mismatch_msgs = []
        for p in manifest_paths:
            stored = stored_hashes.get(p)
            current = local_hashes.get(p)
            if not current:
                mismatch = True
                mismatch_msgs.append(f"{p}: current file missing in checkout")
            elif stored != current:
                mismatch = True
                mismatch_msgs.append(f"{p}: file changed after validation")

        if mismatch:
            issue.create_comment("❌ Cannot proceed: manifests have changed since validation:\n\n" + "\n".join(mismatch_msgs) + "\n\nPlease run `@bot check` again.")
            # remove On review and add invalid to be safe
            try:
                pr.remove_from_labels("On review")
            except Exception:
                pass
            try:
                pr.add_to_labels("Invalid manifest")
            except Exception:
                pass
            sys.exit(0)

        # all good -> perform allow or deny
        if "@bot allow" in comment_body:
            try:
                pr.merge(merge_method="squash")
                # update labels
                try:
                    pr.remove_from_labels("On review")
                except Exception:
                    pass
                try:
                    pr.add_to_labels("Approved")
                except Exception:
                    pass
                issue.create_comment("✅ PR approved and merged by moderator.")
            except Exception as e:
                issue.create_comment(f"❌ Failed to merge PR: {e}")
                sys.exit(1)
        else:  # deny
            try:
                pr.edit(state="closed")
                try:
                    pr.remove_from_labels("On review")
                except Exception:
                    pass
                try:
                    pr.add_to_labels("Rejected")
                except Exception:
                    pass
                issue.create_comment("❌ PR rejected and closed by moderator.")
            except Exception as e:
                issue.create_comment(f"❌ Failed to close PR: {e}")
                sys.exit(1)

        sys.exit(0)

    # unknown command
    issue.create_comment("ℹ️ Unknown command. Supported commands: `@bot check`, `@bot allow`, `@bot deny`.")
    sys.exit(0)

if __name__ == "__main__":
    main()
