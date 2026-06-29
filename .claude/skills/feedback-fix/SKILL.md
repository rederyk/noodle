---
name: feedback-fix
description: >-
  Pick up a local user feedback report from feedback/, reproduce and fix the
  issue safely with git checkpoints, and prepare a PR with the mandatory AI-use
  disclosure. Use when the user points you at a feedback report, says "triage
  feedback/", or asks you to fix something they reported in-app.
---

# feedback-fix

The noodle app can save a user feedback/report drop under **`feedback/<id>/`**
(enabled via ⚙ Settings → "Show the Feedback / report button"). Your job is to
turn one of those into a safe, reviewable fix and a PR.

**This skill is intentionally short. The full procedure — git safety, repo reload
rules, PR contents, and the mandatory AI disclosure — lives in
[`docs/FEEDBACK_FIX_GUIDE.md`](../../../docs/FEEDBACK_FIX_GUIDE.md). Read it before
touching code.**

## Steps (each points into the guide)

1. **Read the report.** Open the newest `feedback/<id>/report.md` (human-readable)
   and `report.json` (structured). Note the project, severity, message, and the
   per-node errors. → guide §1 "Dove sono i report".

2. **Reproduce.** If `graph.snapshot.json` is present, load/execute it to see the
   problem first-hand (host venv or the live API). → guide §1 + `CLAUDE.md` §2.

3. **Make a safe workspace BEFORE editing code.** Confirm a clean tree, then cut a
   dedicated PR branch `fix/feedback-<id>` from the user's personal branch
   (`user/<name>`, which aggregates all their changes); checkpoint as you go so you
   can always return to a safe state. One PR = one branch. → guide §2 "Git in sicurezza".

4. **Fix, respecting repo rules.** Restart after backend changes, hard-refresh the
   frontend, keep the wire tables in sync, run the tests. → guide §3 + `CLAUDE.md` §6.

5. **Open the PR using the template.** Reference the report id (don't commit the
   `feedback/` files), include repro + tests, and **always disclose which coding
   agents and models you used.** → guide §4–§5 + `.github/PULL_REQUEST_TEMPLATE.md`.

Never commit the `feedback/` directory (it's gitignored and may hold the user's
project details) and never include secrets or raw session data without scrubbing.
