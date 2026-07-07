#!/bin/bash
# Create a FlightScnr Pi release (year.month.day.iteration).
#
# Bumps VERSION, commits, tags, and optionally pushes to GitHub.
# Pushing the tag triggers .github/workflows/release.yml to publish
# a GitHub Release for public installs / the web portal updater.
#
# Usage:
#   ./scripts/release.sh              # bump for today (or next iteration same day)
#   ./scripts/release.sh --dry-run    # show next version only
#   ./scripts/release.sh --iteration 3  # force 2026.7.7.3 (uses today's date)
#   ./scripts/release.sh --push       # commit, tag, and push to origin
#   ./scripts/release.sh --message "Fix radar range sync"
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION_FILE="$REPO_ROOT/VERSION"

DRY_RUN=0
PUSH=0
ITERATION=""
MESSAGE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --push) PUSH=1 ;;
        --iteration)
            shift
            ITERATION="${1:-}"
            if [ -z "$ITERATION" ]; then
                echo "Missing value for --iteration" >&2
                exit 1
            fi
            ;;
        --message|-m)
            shift
            MESSAGE="${1:-}"
            if [ -z "$MESSAGE" ]; then
                echo "Missing value for --message" >&2
                exit 1
            fi
            ;;
        -h|--help)
            sed -n '2,14p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 1
            ;;
    esac
    shift
done

if [ ! -d "$REPO_ROOT/.git" ]; then
    echo "Not a git repository: $REPO_ROOT" >&2
    exit 1
fi

if [ -n "$(git -C "$REPO_ROOT" status --porcelain)" ]; then
    echo "Working tree has uncommitted changes. Commit or stash first." >&2
    git -C "$REPO_ROOT" status --short >&2
    exit 1
fi

CURRENT=""
if [ -f "$VERSION_FILE" ]; then
    CURRENT="$(tr -d '[:space:]' < "$VERSION_FILE")"
fi

NEXT_VERSION="$(python3 <<PY
import os, sys
sys.path.insert(0, os.path.join("$REPO_ROOT", "flightscnr"))
from datetime import date
from version import bump_version

today = date.today()
kwargs = {"today": (today.year, today.month, today.day)}
if "$ITERATION":
    kwargs["iteration"] = int("$ITERATION")
print(bump_version("$CURRENT" or None, **kwargs))
PY
)"

echo "Current: ${CURRENT:-<none>}"
echo "Next:    $NEXT_VERSION"

if [ "$DRY_RUN" -eq 1 ]; then
    exit 0
fi

if git -C "$REPO_ROOT" rev-parse -q --verify "refs/tags/$NEXT_VERSION" >/dev/null; then
    echo "Tag already exists: $NEXT_VERSION" >&2
    exit 1
fi

printf '%s\n' "$NEXT_VERSION" > "$VERSION_FILE"
git -C "$REPO_ROOT" add VERSION

TAG_MESSAGE="FlightScnr Pi $NEXT_VERSION"
if [ -n "$MESSAGE" ]; then
    TAG_MESSAGE="$TAG_MESSAGE — $MESSAGE"
fi

git -C "$REPO_ROOT" commit -m "Release $NEXT_VERSION"
git -C "$REPO_ROOT" tag -a "$NEXT_VERSION" -m "$TAG_MESSAGE"

echo ""
echo "Created release $NEXT_VERSION"
echo "  commit: $(git -C "$REPO_ROOT" rev-parse --short HEAD)"
echo "  tag:    $NEXT_VERSION"

if [ "$PUSH" -eq 1 ]; then
    echo ""
    echo "Pushing to origin..."
    git -C "$REPO_ROOT" push origin HEAD
    git -C "$REPO_ROOT" push origin "$NEXT_VERSION"
    echo "GitHub Actions will publish the release for tag $NEXT_VERSION."
else
    echo ""
    echo "Push when ready:"
    echo "  git push origin HEAD && git push origin $NEXT_VERSION"
fi
