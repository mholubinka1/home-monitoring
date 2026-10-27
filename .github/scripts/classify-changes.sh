#!/usr/bin/env bash
# Says what kind of change this push is, so CI only does the work it needs.
#
# A branch is judged as a whole: its tip against its merge-base with origin/main. A
# push to main is judged from where main was before it (PUSH_BEFORE).
# Writes to $GITHUB_OUTPUT:
#   lane=docs|grafana|code  docs: only *.md, .agent-docs/ or docs/ changed, outside
#                                 data/grafana/ and deployments/
#                           grafana: data/grafana/ changed and nothing that is code
#                           code: anything else
#   images=true|false       something that goes into an app image changed
#   changed_md=<paths>      space-separated changed Markdown files that still exist;
#                           every Markdown file if anything was deleted or moved
# If the diff can't be worked out, it says code and images and lists every Markdown
# file: the full run.
set -uo pipefail

output="${GITHUB_OUTPUT:-/dev/stdout}"

full_run() {
  echo "::warning::Could not work out what changed ($1); running everything."
  {
    echo "lane=code"
    echo "images=true"
    echo "changed_md=$(git ls-files '*.md' | tr '\n' ' ' | sed 's/ *$//')"
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
# A deleted (or moved) file can break a link in a Markdown file that did not change,
# and a new link-check config applies to every file, so then every Markdown file gets
# its links checked.
deleted=$("${diff[@]}" --diff-filter=D) || full_run "git diff failed"
if [ -n "$deleted" ] || grep -qx '.markdown-link-check.json' <<<"$changed"; then
  changed_md=$(git ls-files '*.md') || full_run "git ls-files failed"
fi

lane=docs
images=false
while IFS= read -r path; do
  [ -n "$path" ] || continue
  # Directories before the *.md rule: tests read the Markdown under data/grafana/ and
  # deployments/, so changing it is not docs-only.
  case "$path" in
    data/grafana/*) [ "$lane" = docs ] && lane=grafana ;;
    deployments/*) lane=code ;;
    *.md | .agent-docs/* | docs/*) ;;
    *) lane=code ;;
  esac
  case "$path" in
    *.md) ;;
    deployments/* | .dockerignore | uv.lock | pyproject.toml | */pyproject.toml | \
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
