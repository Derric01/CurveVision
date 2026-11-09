# Reviewing and merging other people's pull requests

A checklist for the maintainer. Contributors read [CONTRIBUTING.md](CONTRIBUTING.md); this is
the other side of it.

## Settings that protect the repository (once)

Set these on GitHub once; they make a mistake on a busy day hard to make.

1. **Settings → Actions → General → Fork pull request workflows from outside collaborators:**
   choose **Require approval for all external contributors**. A stranger's pull request then
   runs no CI until you have looked at it and clicked **Approve and run**.
2. **Settings → Actions → General → Workflow permissions:** **Read repository contents and
   packages permissions**, and leave **Allow GitHub Actions to create and approve pull requests**
   unticked. Workflows that need more ask for it themselves.
3. **Settings → Rules → Rulesets → `main`:** require a pull request, require the status checks
   *Web (lint, types, tests, build)* and *Server (lint, types, tests)*, block force pushes and
   deletions. Tick **Require review from Code Owners** (`.github/CODEOWNERS` makes you the
   reviewer of everything), and under **Bypass list** add **Repository admin**, so you can
   still merge your own pull requests while nobody else can skip your review.
4. **Settings → Advanced Security:** keep Dependabot alerts, secret scanning and push
   protection on, and private vulnerability reporting on.

Fork pull requests never receive this repository's secrets: GitHub withholds them from
`pull_request` runs started from a fork, and no workflow here uses `pull_request_target`.
Keep it that way. A workflow that checks out a contributor's code with secrets available is
the most common way open-source projects are compromised.

## Reviewing a pull request

1. **Read the description and the linked issue.** Is this a change the project wants? It is fine
   to say no early and kindly, before anyone spends a week on it.
2. **Look at what it touches before what it says.** Open **Files changed** and check:
   * `.github/workflows/`, `deploy/`, `desktop/`, install scripts, `package.json` /
     `pyproject.toml` / `Cargo.toml` scripts: anything that runs during CI or on a user's
     machine. Read every line.
   * New dependencies: each must be needed, maintained, permissively licensed, pinned in
     `server/constraints.txt` (Python) or a lockfile, and listed in
     `docs/THIRD_PARTY_NOTICES.md`. CI's notices and pinned-version checks enforce the last two.
   * Binary files (images, archives, executables): ask what they are. Never merge an
     unexplained binary.
   * Lockfile changes in a pull request that is not about dependencies: ask why.
3. **Approve and run CI** (the button on the pull request) only once nothing in step 2 worries
   you.
4. **Read the checks.** All of them green, including **Security** (pip-audit, npm audit, cargo
   audit, bandit, Trivy) and **CodeQL**. Code-scanning comments on the pull request are claims to
   check, not orders; a real finding is fixed before merging.
5. **Run it** for anything that changes behaviour: check out the branch
   (`gh pr checkout <number>`) and try the change, or ask for a screenshot or recording.
6. **Tests.** New behaviour comes with a test; a bug fix with a test that fails without it.
   `./scripts/check.sh` is what CI runs.

## Merging

* **Squash and merge**, with a commit message that says what changed and why. The contributor
  stays the author.
* If `main` moved on, ask the contributor to update the branch, or use **Update branch**, and
  let CI run again before merging.
* Thank people. A first contribution that is merged quickly and politely is how a project gets
  a second one.

## Dependabot's pull requests

Dependabot opens grouped pull requests every week. A **security update** (marked as such) is
merged once CI is green. A routine update is merged when CI is green and its changelog shows
nothing alarming. A major version bump gets the same reading as a contributor's change.

## If something bad was merged

Revert it at once (**Revert** on the merged pull request opens a pull request that undoes it),
then investigate. If it touched a release, do not publish the release; if a release was already
published, mark it as a pre-release with a note and publish a fixed version. For anything
security-related, follow `docs/SECURITY.md`.
