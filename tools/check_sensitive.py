#!/usr/bin/env python3
"""Pre-commit / pre-push check for sensitive data (personal and company data, local paths, secrets).

The repository is PUBLIC. A commit or a push must not carry:
  * a person's name or e-mail, a company name, a host or a share name
  * absolute paths of a local machine (C:\\Users\\<name>\\..., /home/<name>, UNC paths)
  * tokens, passwords, private keys

Rules
  1. Built-in, generic rules (below) - they hold no personal string, so this file is safe to publish.
  2. Your own strings (name, company, host, ...): one regular expression per line, case-insensitive,
     in `.git/sensitive-patterns.txt`. That file is inside .git, so it is never committed or pushed.
  3. Allow list: `.git/sensitive-allow.txt`, one `path-regex` per line (files that may contain a
     match, e.g. a licence); and the marker `sensitive-ok` on a line allows that single line.

Modes
  --staged   what is staged for the commit (pre-commit hook)
  --tree     every tracked file of HEAD plus the working tree (audit)
  --push     commits that are about to be pushed: their added lines, messages, author and
             committer identities (pre-push hook; reads the ref list from stdin)
  --history  every commit of every branch (audit before a publication)

Exit code 0 = clean, 1 = findings, 2 = usage error. Install the hooks with `python tools/install_githooks.py`.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 5 * 1024 * 1024

BUILTIN = [
    ("local user path (Windows)", r"[A-Za-z]:[\\/]+Users[\\/]+(?!Public\b|Default\b|All Users\b|<|%|\$|\{|\*|\.\.\.|USER|you\b|name\b|user\b)[^\\/\s\"'<>|:*?]+"),
    ("local user path (Unix)", r"(?:/home|/Users)/(?!runner\b|user\b|name\b|you\b|<|\$)[A-Za-z0-9._-]{2,}/"),
    ("UNC path", r"\\\\(?!\?)[A-Za-z0-9_.-]{3,}\\[A-Za-z0-9$_.-]{2,}"),
    ("e-mail address", r"\b[A-Za-z0-9._%+-]+@(?!(?:users\.noreply\.github\.com|noreply\.github\.com|anthropic\.com|example\.(?:com|org)|localhost)\b)[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"),
    ("GitHub token", r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    ("AWS key id", r"\bAKIA[0-9A-Z]{16}\b"),
    ("Slack token", r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    ("private key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("password assignment", r"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"][^'\"\s]{6,}['\"]"),
]


def git(*args, check=True, text=True, input=None):
    r = subprocess.run(["git", *args], capture_output=True, text=text, input=input, encoding="utf-8" if text else None, errors="replace" if text else None)
    if check and r.returncode:
        raise SystemExit("git %s failed: %s" % (" ".join(args), (r.stderr or "")[-200:]))
    return r.stdout


def repo():
    return Path(git("rev-parse", "--show-toplevel").strip())


def git_dir():
    return Path(git("rev-parse", "--absolute-git-dir").strip())


def load_lines(path):
    if not path.exists():
        return []
    return [l.strip() for l in path.read_text(encoding="utf-8", errors="replace").splitlines()
            if l.strip() and not l.lstrip().startswith("#")]


class Rules:
    def __init__(self):
        gd = git_dir()
        self.rules = [(n, re.compile(p)) for n, p in BUILTIN]
        for i, p in enumerate(load_lines(gd / "sensitive-patterns.txt"), 1):
            self.rules.append(("local pattern #%d" % i, re.compile(p, re.I)))
        self.allow = [re.compile(p) for p in load_lines(gd / "sensitive-allow.txt")]
        self.n_local = len(self.rules) - len(BUILTIN)

    def allowed(self, path):
        return any(a.search(path) for a in self.allow)

    def scan_text(self, label, text, findings):
        for no, line in enumerate(text.splitlines(), 1):
            if "sensitive-ok" in line:
                continue
            for name, rx in self.rules:
                m = rx.search(line)
                if m:
                    s = m.group(0)
                    shown = s[:2] + "*" * max(0, min(len(s), 24) - 4) + s[-2:] if len(s) > 4 else "***"
                    findings.append("%s:%d: %s [%s]" % (label, no, name, shown))


def is_binary(b):
    return b"\0" in b[:8000]


def scan_blob_list(rules, items, findings):
    for label, getter in items:
        if rules.allowed(label):
            continue
        b = getter()
        if b is None or len(b) > MAX_BYTES or is_binary(b):
            continue
        rules.scan_text(label, b.decode("utf-8", "replace"), findings)


def run_staged(rules, findings):
    names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split("\0")
    items = []
    for n in filter(None, names):
        def getter(n=n):
            r = subprocess.run(["git", "show", ":" + n], capture_output=True)
            return r.stdout if r.returncode == 0 else None
        items.append((n, getter))
    scan_blob_list(rules, items, findings)
    ident = "%s <%s>" % (git("config", "user.name").strip(), git("config", "user.email").strip())
    scan_identity(rules, "commit author (git config)", ident, findings)


def run_tree(rules, findings):
    root = repo()
    names = git("ls-files", "-z").split("\0")
    items = []
    for n in filter(None, names):
        def getter(n=n):
            p = root / n
            return p.read_bytes() if p.is_file() else None
        items.append((n, getter))
    scan_blob_list(rules, items, findings)


def scan_identity(rules, label, text, findings):
    rules.scan_text(label, text, findings)


def scan_commit_range(rules, rng, findings, full_content):
    """Added lines, message, author and committer of the commits in `rng`."""
    shas = git("rev-list", *rng).split()
    for sha in shas:
        head = git("log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B", sha)
        rules.scan_text("commit %s (author/committer/message)" % sha[:10], head, findings)
        diff = git("show", "--format=", "--unified=0", "--no-color", "-M", sha, check=False)
        cur = None
        for line in diff.splitlines():
            if line.startswith("+++ b/"):
                cur = line[6:]
            elif line.startswith("+") and not line.startswith("+++") and cur and not rules.allowed(cur):
                if "sensitive-ok" in line:
                    continue
                for name, rx in rules.rules:
                    m = rx.search(line)
                    if m:
                        s = m.group(0)
                        shown = s[:2] + "*" * max(0, min(len(s), 24) - 4) + s[-2:] if len(s) > 4 else "***"
                        findings.append("%s @%s: %s [%s]" % (cur, sha[:10], name, shown))
                        break


def run_push(rules, findings):
    zero = "0" * 40
    data = sys.stdin.read().strip().splitlines()
    for line in data:
        parts = line.split()
        if len(parts) < 4:
            continue
        local_ref, local_sha, remote_ref, remote_sha = parts[:4]
        if local_sha == zero:
            continue                         # deleting a branch
        rng = [local_sha] + (["^" + remote_sha] if remote_sha != zero else ["--not", "--remotes"])
        scan_commit_range(rules, rng, findings, False)


def run_history(rules, findings):
    scan_commit_range(rules, ["--all"], findings, True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    for m in ("staged", "tree", "push", "history"):
        g.add_argument("--" + m, action="store_true")
    ap.add_argument("--max", type=int, default=40, help="findings to print (default 40)")
    a = ap.parse_args()
    rules = Rules()
    findings = []
    mode = [m for m in ("staged", "tree", "push", "history") if getattr(a, m)][0]
    {"staged": run_staged, "tree": run_tree, "push": run_push, "history": run_history}[mode](rules, findings)
    print("sensitive-data check (%s): %d built-in rules, %d local patterns" % (mode, len(BUILTIN), rules.n_local))
    if rules.n_local == 0:
        print("  note: .git/sensitive-patterns.txt is missing or empty - names/company are NOT checked yet")
    if findings:
        print("FOUND %d possible sensitive item(s):" % len(findings))
        for f in findings[: a.max]:
            print("  " + f)
        if len(findings) > a.max:
            print("  ... and %d more (use --max)" % (len(findings) - a.max))
        print("Fix them (replace with a placeholder), or mark a line with `sensitive-ok`, or add the file to .git/sensitive-allow.txt.")
        return 1
    print("clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
