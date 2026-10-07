"""Where the data lives.

One rule: data goes in the MAIN checkout's data/ folder, never in a worktree's.

On the box the main checkout is /ygg/projects/wbg-digital-resilience, and every
board card works in /ygg/projects/wbg-digital-resilience/.worktrees/<task-id>.
That worktree is deleted when the card completes. A script that wrote to
"data/ next to the code" would write into the worktree and the data would go
with it. So the data root is found through git: the shared .git directory sits
in the main checkout, whichever worktree the code is run from.

Order of precedence:
  1. $WBG_DATA, if set
  2. <main checkout>/data, found with `git rev-parse --git-common-dir`
  3. <this checkout>/data, when git is unavailable
"""
import os
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main_checkout(repo=REPO):
    try:
        out = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return repo
    if not out or os.path.basename(out) != ".git":
        return repo
    return os.path.dirname(out)


def data_root():
    env = os.environ.get("WBG_DATA", "").strip()
    if env:
        return os.path.abspath(env)
    return os.path.join(main_checkout(), "data")
