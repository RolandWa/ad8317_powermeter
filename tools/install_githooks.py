#!/usr/bin/env python3
"""Install the sensitive-data hooks into .git/hooks (pre-commit and pre-push).

    python tools/install_githooks.py

The hooks call tools/check_sensitive.py. Create `.git/sensitive-patterns.txt` with your own strings
(see the docstring of check_sensitive.py). Skip a hook only on purpose: `git commit --no-verify`.
"""
import stat
import subprocess
import sys
from pathlib import Path

HOOK = """#!/bin/sh
# installed by tools/install_githooks.py - sensitive data check before {what}
exec python "$(git rev-parse --show-toplevel)/tools/check_sensitive.py" {mode}
"""


def main():
    root = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())
    hooks = Path(subprocess.check_output(["git", "rev-parse", "--absolute-git-dir"], text=True).strip()) / "hooks"
    hooks.mkdir(exist_ok=True)
    for name, what, mode in (("pre-commit", "a commit", "--staged"), ("pre-push", "a push", "--push")):
        p = hooks / name
        if p.exists() and "check_sensitive.py" not in p.read_text(errors="replace"):
            p.rename(hooks / (name + ".old"))
            print("kept the old hook as %s.old" % name)
        p.write_text(HOOK.format(what=what, mode=mode), newline="\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
        print("installed", p)
    pat = hooks.parent / "sensitive-patterns.txt"
    if not pat.exists():
        pat.write_text("# one regular expression per line, case-insensitive; this file stays in .git (never pushed)\n")
        print("created", pat, "- put your name, company, host names here")
    print("test:  python tools/check_sensitive.py --tree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
