"""
GitHub Upload Script

Automates uploading the xauusd_signal_master project to GitHub:
  1. Creates a new public repo on your GitHub account (via API)
  2. Initializes git in the project folder
  3. Creates .gitignore (already exists, but verifies)
  4. Adds LICENSE and CREDITS.md
  5. Commits everything
  6. Pushes to GitHub

Usage:
  python upload_to_github.py

Requires:
  - Git installed (https://git-scm.com/download/win)
  - GitHub Personal Access Token (PAT) with 'repo' scope
  - Run from the xauusd_signal_master folder

Step-by-step token creation:
  1. Go to https://github.com/settings/tokens
  2. Click "Generate new token (classic)"
  3. Note: "XAUUSD Upload"
  4. Expiration: 30 days (or whatever you prefer)
  5. Scopes: tick "repo" (full control of private repositories)
  6. Generate token
  7. Copy the token (you only see it ONCE)
  8. Paste when this script asks for it
"""

import os
import sys
import json
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from getpass import getpass


# ============================================================================
# CONFIGURATION - EDIT THESE
# ============================================================================

REPO_NAME = "xauusd-signal-master"      # GitHub repo name
REPO_DESCRIPTION = "Automated XAUUSD signal provider system - Sunrise Ogle strategy on MetaTrader 5 with FundingPips compliance"
REPO_PRIVATE = False                     # False = public, True = private
LICENSE_TEXT_FILENAME = "LICENSE"
CREDITS_FILENAME = "CREDITS.md"

# Sunrise Ogle attribution (REQUIRED - the strategy is MIT-licensed by another author)
SUNRISE_OGLE_REPO_URL = "https://github.com/ilahuerta-IA/backtrader-pullback-window-xauusd"
SUNRISE_OGLE_AUTHOR = "ilahuerta-IA"


# ============================================================================
# UTILITIES
# ============================================================================

def run(cmd, capture=True, check=True):
    """Run a shell command and return its output."""
    print(f"  $ {cmd}")
    result = subprocess.run(
        cmd, shell=True, capture_output=capture, text=True, encoding='utf-8'
    )
    if capture and result.stdout:
        for line in result.stdout.strip().split('\n')[:5]:
            print(f"    {line}")
    if check and result.returncode != 0:
        if result.stderr:
            print(f"    ERROR: {result.stderr.strip()}")
        sys.exit(1)
    return result


def github_api_request(method, url, token, data=None):
    """Make an authenticated GitHub API request."""
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
        'User-Agent': 'XAUUSD-Upload-Script',
    }
    body = None
    if data is not None:
        body = json.dumps(data).encode('utf-8')
        headers['Content-Type'] = 'application/json'

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='ignore')
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {'message': body}
    except Exception as e:
        return -1, {'message': str(e)}


# ============================================================================
# LICENSE AND CREDITS GENERATION
# ============================================================================

LICENSE_TEXT = """MIT License

Copyright (c) {year} {your_name}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

CREDITS_TEXT = """# Credits

This project builds on the work of others. Credit where credit is due.

## Strategy

The Sunrise Ogle XAU/USD pullback strategy was developed by
**[{author}]({sunrise_repo})**.

This project does NOT include the strategy code itself — it imports it from
the original repository as an external dependency. To use this project, you
must clone the strategy repo alongside:

```bash
git clone {sunrise_repo} sunrise_ogle
```

The strategy is licensed under MIT — see the original repo for details.

## What This Project Adds

- Live trading wrapper around the Backtrader strategy (signal-mode)
- MT5 data feed integration via the official MetaTrader5 Python package
- HTTP REST + WebSocket signal broadcaster
- MT5 Expert Advisor (MQL5) signal subscriber
- FundingPips prop firm compliance guards
- ForexFactory news filter
- Telegram bot notifications
- Streamlit real-time dashboard
- Paper trading mode for safe forward testing

## Other Dependencies

- **MetaTrader5** Python package (MetaQuotes Software Corp.)
- **Backtrader** framework (https://www.backtrader.com/)
- **FastAPI** for HTTP API (https://fastapi.tiangolo.com/)
- **Streamlit** for dashboard (https://streamlit.io/)
- **websockets** for real-time push
- **pyyaml**, **pandas**, **numpy** standard data tools

## Disclaimer

Trading financial instruments involves substantial risk of loss. Past
performance does not guarantee future results. This software is for
educational and research purposes. Test thoroughly before live trading.
"""


def write_license(your_name: str):
    """Write LICENSE file."""
    from datetime import datetime
    text = LICENSE_TEXT.format(
        year=datetime.now().year,
        your_name=your_name,
    )
    Path(LICENSE_TEXT_FILENAME).write_text(text, encoding='utf-8')
    print(f"  Created {LICENSE_TEXT_FILENAME}")


def write_credits():
    """Write CREDITS.md file."""
    text = CREDITS_TEXT.format(
        author=SUNRISE_OGLE_AUTHOR,
        sunrise_repo=SUNRISE_OGLE_REPO_URL,
    )
    Path(CREDITS_FILENAME).write_text(text, encoding='utf-8')
    print(f"  Created {CREDITS_FILENAME}")


# ============================================================================
# MAIN UPLOAD FLOW
# ============================================================================

def main():
    print("=" * 60)
    print(" XAUUSD Signal Master - GitHub Upload Script")
    print("=" * 60)
    print()

    # 1. Verify we're in the right folder
    if not Path("master").exists() or not Path("requirements.txt").exists():
        print("ERROR: Run this script from the xauusd_signal_master folder.")
        print("Current folder:", Path.cwd())
        sys.exit(1)

    print(f"Project folder: {Path.cwd()}")
    print()

    # 2. Get user info
    print("Please provide your GitHub credentials:")
    github_user = input("  GitHub username (e.g. 'kokkli'): ").strip()
    if not github_user:
        print("ERROR: username required")
        sys.exit(1)

    your_name = input("  Your name for the LICENSE file (e.g. 'Janika Kokkli'): ").strip()
    if not your_name:
        your_name = github_user

    print()
    print("Now you need a GitHub Personal Access Token (PAT).")
    print("Steps:")
    print("  1. Go to https://github.com/settings/tokens")
    print("  2. Click 'Generate new token (classic)'")
    print("  3. Name: 'XAUUSD Upload', expiration: 30 days")
    print("  4. Scopes: tick 'repo'")
    print("  5. Generate, then copy the token (shown only once!)")
    print()
    token = getpass("  Paste your GitHub token (input hidden): ").strip()
    if not token:
        print("ERROR: token required")
        sys.exit(1)

    print()
    print(f"Repository: {github_user}/{REPO_NAME}")
    print(f"Visibility: {'private' if REPO_PRIVATE else 'public'}")
    print()
    confirm = input("Proceed? [y/N]: ").strip().lower()
    if confirm != 'y':
        print("Aborted.")
        sys.exit(0)

    # 3. Verify git is available
    print()
    print("[1/8] Checking Git installation...")
    result = subprocess.run(
        "git --version", shell=True, capture_output=True, text=True
    )
    if result.returncode != 0:
        print("ERROR: Git not installed. Get it from https://git-scm.com/")
        sys.exit(1)
    print(f"  Git OK: {result.stdout.strip()}")

    # 4. Create LICENSE and CREDITS
    print()
    print("[2/8] Creating LICENSE and CREDITS.md...")
    write_license(your_name)
    write_credits()

    # 5. Verify .gitignore exists
    print()
    print("[3/8] Checking .gitignore...")
    if not Path(".gitignore").exists():
        Path(".gitignore").write_text(
            "venv/\n__pycache__/\n*.pyc\n.env\nlogs/\ndata/*.json\n"
            "temp_reports/\n*.ex5\n",
            encoding='utf-8'
        )
        print("  Created basic .gitignore")
    else:
        print("  .gitignore already exists")

    # 6. Create the repo on GitHub via API
    print()
    print(f"[4/8] Creating GitHub repo '{REPO_NAME}'...")
    status, response = github_api_request(
        'POST',
        'https://api.github.com/user/repos',
        token,
        data={
            'name': REPO_NAME,
            'description': REPO_DESCRIPTION,
            'private': REPO_PRIVATE,
            'auto_init': False,
        }
    )

    if status == 201:
        clone_url = response['clone_url']
        html_url = response['html_url']
        print(f"  Created: {html_url}")
    elif status == 422 and 'already exists' in str(response).lower():
        # Repo already exists - that's fine, we'll push to it
        print(f"  Repo already exists (status 422), will push to existing repo")
        clone_url = f"https://github.com/{github_user}/{REPO_NAME}.git"
        html_url = f"https://github.com/{github_user}/{REPO_NAME}"
    else:
        print(f"  ERROR creating repo: status={status}")
        print(f"  Response: {response}")
        sys.exit(1)

    # Build URL with embedded token (for git push without prompting)
    push_url = clone_url.replace('https://', f'https://{github_user}:{token}@')

    # 7. Initialize git locally
    print()
    print("[5/8] Initializing local git repo...")
    if Path(".git").exists():
        print("  .git folder already exists — using existing repo")
    else:
        run("git init")
        run("git branch -M main")

    # Configure user (needed for first commit)
    print()
    print("[6/8] Configuring git user...")
    run(f'git config user.name "{your_name}"', check=False)
    run(f'git config user.email "{github_user}@users.noreply.github.com"', check=False)

    # 8. Add, commit, push
    print()
    print("[7/8] Staging and committing files...")
    run("git add -A")
    # Check if there's anything to commit
    result = subprocess.run(
        "git diff --cached --quiet", shell=True, capture_output=True
    )
    if result.returncode == 0:
        print("  Nothing to commit (all files already committed)")
    else:
        run('git commit -m "Initial commit - XAUUSD Signal Master MVP"')

    print()
    print("[8/8] Pushing to GitHub...")
    # Remove existing 'origin' if any
    subprocess.run(
        "git remote remove origin", shell=True, capture_output=True
    )
    run(f'git remote add origin {push_url}', check=False)
    result = subprocess.run(
        "git push -u origin main", shell=True, capture_output=True, text=True
    )
    if result.returncode != 0:
        # Try 'master' branch instead
        print("  Trying push to 'master' branch...")
        result = subprocess.run(
            "git push -u origin master", shell=True, capture_output=True, text=True
        )
    if result.returncode != 0:
        print(f"  ERROR pushing: {result.stderr}")
        print()
        print("  Manual fallback - run this in CMD:")
        print(f'  cd "{Path.cwd()}"')
        print(f"  git push -u origin main")
        sys.exit(1)

    print(f"  Pushed successfully!")

    # Clean up: remove token from remote URL
    subprocess.run(
        "git remote remove origin", shell=True, capture_output=True
    )
    subprocess.run(
        f"git remote add origin {clone_url}", shell=True, capture_output=True
    )

    # Done
    print()
    print("=" * 60)
    print(" SUCCESS!")
    print("=" * 60)
    print()
    print(f"Your repository is live at:")
    print(f"  {html_url}")
    print()
    print("Next steps:")
    print(f"  1. Visit your repo and verify everything is there")
    print(f"  2. Make sure you've also forked the Sunrise Ogle repo:")
    print(f"     {SUNRISE_OGLE_REPO_URL}")
    print(f"  3. The README.md will tell anyone how to use the project")
    print()
    print("To make future updates:")
    print("  git add -A")
    print('  git commit -m "your message"')
    print("  git push")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAborted by user.")
        sys.exit(1)
