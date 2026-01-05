#!/usr/bin/env python3

import os
import json
import sys
import hashlib
import requests
from github import Github
from PIL import Image

ICON_MAX_SIZE = 3072
ICON_WIDTH = 32
ICON_HEIGHT = 32

REQUIRED_FIELDS = [
    "package",
    "name",
    "author",
    "version",
    "category",
    "description",
    "url",
    "sha256",
    "api_level",
    "permissions",
    "min_os_version"
]

def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()

def load_event():
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path or not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_pr_number(event):
    if "pull_request" in event:
        return event["pull_request"]["number"]
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/pull/"):
        return int(ref.split("/")[2])
    return None

def main():
    token = os.environ.get("GITHUB_TOKEN")
    repo_name = os.environ.get("GITHUB_REPOSITORY")

    if not token or not repo_name:
        print("Missing GitHub environment variables")
        sys.exit(1)

    gh = Github(token)
    repo = gh.get_repo(repo_name)

    event = load_event()
    pr_number = get_pr_number(event)

    if not pr_number:
        print("No pull request context")
        sys.exit(0)

    pr = repo.get_pull(pr_number)
    issue = repo.get_issue(pr_number)

    changed_files = list(pr.get_files())
    manifests = [
        f.filename
        for f in changed_files
        if f.filename.startswith("manifests/")
        and f.filename.endswith(".json")
    ]

    if not manifests:
        print("No manifests to validate")
        sys.exit(0)

    errors = []

    for manifest_path in manifests:
        if not os.path.exists(manifest_path):
            errors.append(f"❌ {manifest_path}: file not found")
            continue

        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
        except Exception as e:
            errors.append(f"❌ {manifest_path}: invalid JSON ({e})")
            continue

        for field in REQUIRED_FIELDS:
            if field not in manifest:
                errors.append(f"❌ {manifest_path}: missing required field '{field}'")

        package = manifest.get("package")
        url = manifest.get("url")
        declared_hash = manifest.get("sha256")

        icon_path = f"icons/{package}.png"

        if not os.path.exists(icon_path):
            errors.append(f"❌ {manifest_path}: icon file missing ({icon_path})")
        else:
            try:
                size = os.path.getsize(icon_path)
                if size > ICON_MAX_SIZE:
                    errors.append(f"❌ {icon_path}: file size exceeds 2048 bytes")

                with Image.open(icon_path) as img:
                    if img.format != "PNG":
                        errors.append(f"❌ {icon_path}: icon is not PNG")
                    if img.width != ICON_WIDTH or img.height != ICON_HEIGHT:
                        errors.append(f"❌ {icon_path}: icon size must be 32x32")
            except Exception as e:
                errors.append(f"❌ {icon_path}: failed to read icon ({e})")

        if url:
            try:
                r = requests.get(url, timeout=20)
                if r.status_code != 200:
                    errors.append(f"❌ {manifest_path}: URL returned HTTP {r.status_code}")
                else:
                    actual_hash = sha256_bytes(r.content)
                    if actual_hash != declared_hash:
                        errors.append(
                            f"❌ {manifest_path}: sha256 mismatch (expected {declared_hash}, got {actual_hash})"
                        )
            except Exception as e:
                errors.append(f"❌ {manifest_path}: failed to download file ({e})")

    labels = [l.name for l in pr.get_labels()]

    if errors:
        if "On review" in labels:
            pr.remove_from_labels("On review")
        if "Invalid manifest" not in labels:
            pr.add_to_labels("Invalid manifest")

        issue.create_comment(
            "Manifest validation failed:\n\n"
            + "\n".join(errors)
            + "\n\nFix the issues and comment `@bot check` to re-run validation."
        )
        sys.exit(1)

    if "Invalid manifest" in labels:
        pr.remove_from_labels("Invalid manifest")
    if "On review" not in labels:
        pr.add_to_labels("On review")

    issue.create_comment(
        "✅ Manifest validation successful.\n"
        "The manifest is now locked for moderator review."
    )

    sys.exit(0)

if __name__ == "__main__":
    main()
