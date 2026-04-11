---
name: owl-plan-author
description: Use this skill when the user asks to "write an Owl plan", "queue a plan", "add to Owl queue", "create a new Owl plan", "draft a plan for Owl", or any variation of adding a new plan to the Owl queue. Walks Claude through plan numbering, self-contained structure, frontmatter, anchoring, and verification.
---

# Owl Plan Author

Follow this checklist in order every time you author a new Owl plan. Do not skip steps.

---

## Step 1 — Pick the next plan number

**Always inspect BOTH `plan/` and `plan/done/`** before choosing a number.

```sh
ls /Users/lanabarreto/Documents/Murilo/owl/plan/ /Users/lanabarreto/Documents/Murilo/owl/plan/done/
```

- Note the highest numeric prefix across **both** directories combined.
- The next free number is `max(existing) + 1`. No reuse, no filling gaps.
- Zero-pad to 3 digits.

**Worked example:** If `plan/` contains `023-…`, `026-…`, `027-…` and `plan/done/` ends at `028-….done.md`, the next free number is **029**, not 028 and not 024.

The most common mistake is only checking `plan/` and missing numbers already consumed in `plan/done/`.

---

## Step 2 — Name the file

Format: `NNN-kebab-case-short-title.md`

- NNN = zero-padded 3-digit number from Step 1.
- Kebab-case, lowercase ASCII letters/digits/hyphens only.
- Title < 60 characters, descriptive.
- No spaces, no uppercase, no accents.

Drop the file into `owl/plan/`. Never put it in `owl/plan/done/` — that directory is Owl-owned.

---

## Step 3 — Write frontmatter (optional fields)

All fields are optional. Include only what applies.

```markdown
---
review-rounds: 2
priority: low
base-branch: owl/021-some-earlier-plan
---
```

**`review-rounds: N`** — integer, clamped to `[1, 3]`. Default is 2.
- Use `1` for small isolated changes (single file, <50 LOC).
- Use `2` (or omit) for default-size plans.
- Use `3` for large multi-repo refactors with significant risk.

**`priority: low`** — marks as low-priority. With `--skip-low-priority` (or `OWL_SKIP_LOW_PRIORITY=1`), these plans are bypassed on every cycle and only drain when the flag is dropped. Use for nice-to-haves that shouldn't compete with active work.

**`base-branch: <branch-name>`** — start from this branch instead of `main`. Use when this plan depends on code from another plan that is still queued or in-flight. If the named branch has already merged and been deleted on origin, Owl silently falls back to `main`.

---

## Step 4 — Write the plan body

Owl agents have **no context from your session**. The plan must be fully self-contained. Write it as if handing it to a fresh agent who has never seen this conversation.

Required sections, in this order:

### 1. Context
Why this change is being made. What problem does it solve? What prompted it? Intended outcome.

### 2. Working directory
Explicit path(s) to the repo(s). For multi-repo plans, list all repos and note they must be modified together.

> Example: `server at /Users/lanabarreto/Documents/Murilo/saudade, mobile at /Users/lanabarreto/Documents/Murilo/saudade-mobile. Both repos must be modified together.`

### 3. Existing files to anchor on
File paths + approximate line numbers for code the plan depends on or will modify. Include short snippets when context matters. **Read the files before writing these — never guess line numbers.**

### 4. What to change
Concrete instructions, file by file. Each file gets its own subsection: symbols to add, modify, or remove. Include code snippets for non-trivial additions (especially new signatures or helpers).

### 5. What does NOT change
Explicit out-of-scope list. Prevents the agent from wandering into unrelated refactors.

### 6. Files to modify
Flat list of every file path touched by this plan. Group by repo / layer (server / mobile / tests).

### 7. Verification
How to confirm the plan worked end-to-end:
- Automated steps: compile, `pytest`, `tsc --noEmit`, grep for specific strings.
- At least one manual test for UI changes.

### 8. Risks and known limitations
Edge cases, migration concerns, things intentionally left out that might surface during review.

---

## Step 5 — Anchor rules

- **Read referenced files before writing any line numbers.** Never guess.
- Write "around line 130" or "lines 130–145" rather than an exact number — small drift is expected.
- Grep first, then cite: before claiming function `foo` exists at `path/bar.ts:120`, run grep to verify.
- Stale anchors are the #1 cause of plan-execution failure.

---

## Step 6 — Self-containment check

Re-read the draft. Ask: "Could a fresh agent with no conversation history execute this?"

- No references to "as we discussed" or "the thing from earlier" — inline the actual context.
- Every path is absolute.
- Every referenced function/file was verified to exist (grep/read confirmed).

---

## Step 7 — Multi-repo / lockstep rules

- If the plan changes an **API contract**, server and mobile must ship together. State "Both repos must be modified together" near the top of the plan.
- If the plan touches **only one repo**, state so explicitly so reviewers don't hunt for missing changes.
- Owl commits each repo separately and opens separate PRs. It does NOT synchronize merge timing — that's the operator's job.

---

## Step 8 — Queue hygiene

- Drop the file into `owl/plan/` only.
- **Never** rename or renumber a plan already in `plan/` or `plan/done/`. If a number is wrong, leave it and pick the next free one for any fix.
- Committing the plan file to the owl repo is good hygiene but not required for Owl to execute it — Owl reads the filesystem, not git history.
