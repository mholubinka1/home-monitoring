#!/usr/bin/env bash
# Says what kind of change this push is, so CI only does the work it needs.
#
# A branch is judged as a whole: its tip against its merge-base with origin/main. A
# commit on main is judged against its first parent (the main it was merged into).
# Writes to $GITHUB_OUTPUT:
#   lane=docs|grafana|code  docs: only *.md, .agent-docs/ or docs/ changed
#                           grafana: data/grafana/ changed and nothing that is code
#                           code: anything else
#   images=true|false       something that goes into an app image changed
#   changed_md=<paths>      space-separated changed Markdown files that still exist
# If the diff can't be worked out, it says code and images: the full run.
set -uo pipefail

output="${GITHUB_OUTPUT:-/dev/stdout}"

full_run() {
  echo "::warning::Could not work out what changed ($1); running everything."
  {
    echo "lane=code"
    echo "images=true"
    echo "changed_md="
  } >>"$output"
  exit 0
}

if [ "${GITHUB_REF_NAME:-}" = "main" ]; then
  # A push can carry several commits (a rebase-merge, a direct push): judge them all,
  # from the tip main had before the push. A new or unknown `before` (all zeros on a
  # branch's first push, or gone after a force-push) falls back to the first parent.
  before="${PUSH_BEFORE:-}"
  if [ -n "$before" ] && git cat-file -e "${before}^{commit}" 2>/dev/null; then
    base=$before
  else
    base=$(git rev-parse --verify --quiet HEAD^1) || full_run "no parent commit"
  fi
else
  base=$(git merge-base origin/main HEAD 2>/dev/null) || full_run "no merge-base with origin/main"
fi
# --no-renames: a moved file counts at both its old and new path, so moving code into
# docs/ is still a code change. quotePath off: non-ASCII paths come out as they are.
# Paths with spaces are not supported in changed_md (none exist in this repo).
diff=(git -c core.quotePath=false diff --no-renames --name-only "$base" HEAD)
changed=$("${diff[@]}") || full_run "git diff failed"
changed_md=$("${diff[@]}" --diff-filter=d -- '*.md') || full_run "git diff failed"

lane=docs
images=false
while IFS= read -r path; do
  [ -n "$path" ] || continue
  case "$path" in
    *.md | .agent-docs/* | docs/*) ;;
    data/grafana/*) [ "$lane" = docs ] && lane=grafana ;;
    *) lane=code ;;
  esac
  case "$path" in
    *.md) ;;
    deployments/* | uv.lock | pyproject.toml | */pyproject.toml | \
      apps/octopus-app/octopus_app/* | apps/hive-app/hive_app/* | libs/common/common/*)
      images=true
      ;;
  esac
done <<<"$changed"

{
  echo "lane=$lane"
  echo "images=$images"
  echo "changed_md=$(echo "$changed_md" | tr '\n' ' ' | sed 's/ *$//')"
} >>"$output"
