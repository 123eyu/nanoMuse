#!/usr/bin/env bash
# Move every version file of nanoMuse to the next version, for the release commit.
#
#   scripts/release-bump.sh <old> <new> <old-code> <new-code> [<relay-old> <relay-new>]
#   scripts/release-bump.sh 0.1.40 0.1.41 41 42            # the apps and the runtime
#   scripts/release-bump.sh 0.1.40 0.1.41 41 42 0.22.0 0.23.0   # and the relay, when it changed
#   scripts/release-bump.sh --check 0.1.40 0.1.41 41 42    # only say what would move
#
# Edits, in place: scripts/rebrand.py (VERSION_NAME / VERSION_CODE; run it afterwards, it writes
# the Android versionName/versionCode and the iOS MARKETING_VERSION), nanomuse/__init__.py,
# pyproject.toml, CITATION.cff (version and today's date-released), the relay console's
# VERSION, web/ and the two harness/* package.json files with their lock files, and, with the
# last two arguments, cloud/nanomuse_cloud/__init__.py and cloud/pyproject.toml. Run from the
# repository root; the release recipe is in CONTRIBUTING.md.
#
# Every pattern must match exactly one line of its file before anything is written; a file
# that has already moved, or changed shape, stops the run with its name and the pattern, and
# no file is touched. --check runs that pass alone.
set -euo pipefail
usage() { sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; }

check=0
case "${1:-}" in
  -h|--help) usage; exit 0 ;;
  --check) check=1; shift ;;
esac
case "${1:-}" in -*) echo "unknown option $1 (try --help)" >&2; exit 2 ;; esac
[ $# -eq 4 ] || [ $# -eq 6 ] || { usage >&2; exit 2; }
old="$1"; new="$2"; oc="$3"; nc="$4"; rold="${5:-}"; rnew="${6:-}"
[ -f pyproject.toml ] && [ -f scripts/rebrand.py ] || { echo "run from the repository root" >&2; exit 2; }
for v in "$old" "$new" "$rold" "$rnew"; do
  [ -z "$v" ] || [[ "$v" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "not a version: $v (x.y.z)" >&2; exit 2; }
done
for c in "$oc" "$nc"; do
  [[ "$c" =~ ^[0-9]+$ ]] || { echo "not a version code: $c (an integer)" >&2; exit 2; }
done

# in-place sed on GNU (Linux) and BSD (macOS) alike: BSD's -i wants a suffix argument
if sed --version >/dev/null 2>&1; then sedi() { sed -i -E "$@"; }; else sedi() { sed -i '' -E "$@"; }; fi
# a version as a literal inside an extended regular expression
old_re="${old//./\\.}"; rold_re="${rold//./\\.}"

# bump FILE REGEX REPLACEMENT: the one line matching REGEX becomes REPLACEMENT. In the count
# pass a file whose match count is not one is reported and the run fails before any write.
mode=count; failed=0
bump() {
  local file="$1" re="$2" repl="$3" n
  if [ ! -f "$file" ]; then echo "$file: no such file" >&2; failed=1; return; fi
  n="$(grep -cE -- "$re" "$file" || true)"
  if [ "$mode" = count ]; then
    if [ "$n" = 1 ]; then
      echo "$file: 1 line matches /$re/"
    else
      echo "$file: $n lines match /$re/, expected 1 (already bumped, or the file changed shape)" >&2
      failed=1
    fi
    return
  fi
  sedi "s/$re/$repl/" "$file"
}

# A lock file names its own package twice (the root entry and packages[""]) and may list
# dependencies that happen to carry the same version string, so only those two lines move.
lock() {
  python3 - "$1" "$old" "$new" "$mode" <<'EOF'
import sys

path, old, new, mode = sys.argv[1:5]
lines = open(path, encoding="utf-8").read().split("\n")
moved = 0
for i, line in enumerate(lines):
    own = i < 5 or '"": {' in "\n".join(lines[max(0, i - 3):i])
    if own and line.strip() == f'"version": "{old}",':
        lines[i] = line.replace(old, new)
        moved += 1
if moved != 2:
    sys.exit(f'{path}: {moved} own version lines found, expected 2 (the root entry and packages[""])')
print(f"{path}: 2 own version lines")
if mode == "write":
    open(path, "w", encoding="utf-8").write("\n".join(lines))
EOF
}

plan() {
  bump scripts/rebrand.py "^VERSION_NAME = \"$old_re\"$" "VERSION_NAME = \"$new\""
  bump scripts/rebrand.py "^VERSION_CODE = $oc$" "VERSION_CODE = $nc"
  bump nanomuse/__init__.py "^__version__ = \"$old_re\"$" "__version__ = \"$new\""
  bump pyproject.toml "^version = \"$old_re\"$" "version = \"$new\""
  bump CITATION.cff "^version: $old_re$" "version: $new"
  bump CITATION.cff "^date-released: .*$" "date-released: $(date +%F)"
  bump cloud/nanomuse_cloud/console/app.js "const VERSION = \"$old_re\";" "const VERSION = \"$new\";"
  for pkg in web/package.json harness/dsh-nanomuse/package.json harness/desktop/package.json; do
    bump "$pkg" "^  \"version\": \"$old_re\",$" "  \"version\": \"$new\","
  done
  for l in web/package-lock.json harness/desktop/package-lock.json; do
    lock "$l" || failed=1
  done
  if [ -n "$rold" ]; then
    bump cloud/nanomuse_cloud/__init__.py "^__version__ = \"$rold_re\"$" "__version__ = \"$rnew\""
    bump cloud/pyproject.toml "^version = \"$rold_re\"$" "version = \"$rnew\""
  fi
}

plan
[ "$failed" = 0 ] || { echo "nothing written" >&2; exit 1; }
if [ "$check" = 1 ]; then
  echo "check only: every pattern matches once; nothing written"
  exit 0
fi
mode="write"
plan

echo "lock files: $(git diff --numstat -- web/package-lock.json harness/desktop/package-lock.json | awk '{print $1" lines in "$3}' | paste -sd ',' -) (expect 2 each)"
grep -n "^VERSION_NAME\|^VERSION_CODE" scripts/rebrand.py
echo "next: python scripts/rebrand.py  (must print 'clean' on a second run), then scripts/release-docs.py"
