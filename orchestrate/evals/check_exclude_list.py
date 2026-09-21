#!/usr/bin/env python3
"""Deterministic, credit-free check: does the exclude list Setup prescribes actually hide every file Launching writes?

`orchestrate` writes three files that must never reach a peer's PR: the state file at the main checkout root, and
ORCHESTRATOR-BRIEF.md plus claude-start.cmd into each worktree. Setup step 3 says which patterns go into
`.git/info/exclude`. A pattern that names a file the skill never writes protects nothing, and a written file with no
pattern gets committed by a peer's `git add -A` - and the launcher carries --dangerously-skip-permissions.

This script reads the patterns out of SKILL.md, builds a scratch repo plus a linked worktree, applies those patterns
as the repo's info/exclude, drops the three files where the skill writes them, and asks git what it still sees.

usage: python evals/check_exclude_list.py [path/to/SKILL.md]      exit 0 = every file is hidden, exit 1 = leak
"""
import pathlib
import re
import subprocess
import sys
import tempfile

STATE_FILE = ".claude-orchestrator-state.md"
WORKTREE_FILES = ["ORCHESTRATOR-BRIEF.md", "claude-start.cmd"]


def exclude_patterns(skill_text: str) -> list[str]:
    """Backticked names in the Setup step that mentions `.git/info/exclude`."""
    m = re.search(r"`\.git/info/exclude`(.*?)(?=\n\d+\.\s|\n## )", skill_text, re.S)
    if not m:
        sys.exit("FAIL: no Setup step mentioning `.git/info/exclude` found in SKILL.md")
    return re.findall(r"`([^`\s]+)`", m.group(1))


def git(*args, cwd):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout


def main() -> int:
    skill = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).parent.parent / "SKILL.md")
    text = skill.read_text(encoding="utf-8")
    patterns = exclude_patterns(text)
    print("patterns from SKILL.md:", patterns)

    for name in [STATE_FILE, *WORKTREE_FILES]:
        if name not in text:
            print(f"FAIL: SKILL.md no longer mentions {name}; update this check to the file names the skill writes")
            return 1

    with tempfile.TemporaryDirectory() as tmp:
        main_co = pathlib.Path(tmp) / "main"
        main_co.mkdir()
        git("init", "-q", cwd=main_co)
        git("commit", "-q", "--allow-empty", "-m", "init", cwd=main_co)
        wt = pathlib.Path(tmp) / "issue-65"
        git("worktree", "add", "-q", "-b", "feat/x", str(wt), cwd=main_co)
        exclude = pathlib.Path(git("rev-parse", "--git-path", "info/exclude", cwd=main_co).strip())
        exclude = exclude if exclude.is_absolute() else main_co / exclude
        exclude.parent.mkdir(parents=True, exist_ok=True)
        exclude.write_text("\n".join(patterns) + "\n", encoding="utf-8")

        (main_co / STATE_FILE).write_text("x", encoding="utf-8")
        for f in WORKTREE_FILES:
            (wt / f).write_text("x", encoding="utf-8")

        leaks = []
        for where, names in ((main_co, [STATE_FILE]), (wt, WORKTREE_FILES)):
            seen = git("status", "--porcelain", "--untracked-files=all", cwd=where)
            leaks += [n for n in names if n in seen]

    if leaks:
        print(f"FAIL: still visible to `git add -A`: {leaks}")
        return 1
    print("OK: state file, ORCHESTRATOR-BRIEF.md and claude-start.cmd are all hidden in the main checkout and a linked worktree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
