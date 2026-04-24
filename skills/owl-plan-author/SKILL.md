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
ls plan/ plan/done/
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

Note: Owl's actual reviewer count is controlled by local config, not by the plan file. Reviewer slots can now be disabled by setting their provider to `none` or leaving the model blank / setting it to `none` in Owl's private `.env.local`.

**`priority: low`** — marks as low-priority. Each cycle drains normal-priority plans in filename order first, then low-priority plans in filename order — so a `priority: low` plan with a smaller numeric prefix will still run *after* every normal plan, not before. With `--skip-low-priority` (or `OWL_SKIP_LOW_PRIORITY=1`), these plans are bypassed entirely on every cycle and only drain when the flag is dropped. Use for nice-to-haves that shouldn't compete with active work.

**`base-branch: <branch-name>`** — start from this branch instead of `main`. See *Step 3b* below for the complete wiring rules; they are non-obvious enough to deserve their own step.

---

## Step 3b — Wiring plan dependencies with `base-branch`

`base-branch` is the only way to tell Owl "this plan depends on code that another queued/in-flight plan introduces." Getting it right matters — the wrong value silently corrupts the dependent plan's starting state.

### When to use it (and when NOT to)

Use `base-branch` **only** when this plan has a **true code dependency** on another queued or in-flight plan — a symbol it needs to import, a file that must exist, a type it references, a config change another plan ships. If the dependent plan would fail to compile/run without the ancestor plan's code, that is a true dependency and deserves `base-branch`.

Do NOT use `base-branch` just because two plans edit the same file. Non-overlapping edits to the same file merge cleanly via normal git; adding a chain there only creates rigidity. Reserve chains for genuine code dependencies.

### Branch name convention

Owl derives branch names from plan filenames with the pattern `owl/<plan-filename-without-.md>`. So plan `031-mobile-extract-and-test-chat-db-row-mapping.md` lives on branch `owl/031-mobile-extract-and-test-chat-db-row-mapping`. Always use the full branch name in the frontmatter — no abbreviations.

```markdown
---
base-branch: owl/031-mobile-extract-and-test-chat-db-row-mapping
---
```

### Priority rule — low plans cannot depend on normal plans

**A `priority: low` plan MUST NOT declare a `base-branch:` that points at a normal-priority plan.**

Reason: normal-priority plans drain first on every cycle. A normal-priority ancestor will merge (and its branch will auto-delete on origin) long before the low-priority dependent is eligible to run — especially with `--skip-low-priority`, where low plans can sit in the queue for days. By the time the low plan runs, its declared base branch is long gone. Owl's stale-ref fallback will kick in and run the plan against `main`, which usually works but makes the dependency implicit and fragile.

If you genuinely need a chain that crosses this boundary, **either:**
- **Demote the ancestor** to `priority: low` so they drain together in filename order. This is the usual fix for refactor chains. Test-coverage plans, schema splits, and related organization work typically all belong in the low-priority band together.
- **Or drop the base-branch entirely** and add a plain-English gate in the plan body ("This plan requires plan NNN's changes to already be on main. If `lib/foo.ts` does not exist, STOP and wait for NNN to merge first."). Use this when you cannot demote the ancestor for a good reason.

Normal plans can freely depend on normal plans. Low plans can depend on low plans. Never mix the priorities inside a chain.

### Transitive chains: always stack on the immediate predecessor

If plan C depends on B, and B depends on A, then:
- A's frontmatter has no `base-branch:` (it descends from `main`).
- B's frontmatter has `base-branch: owl/<A>`.
- C's frontmatter has `base-branch: owl/<B>` — **not** `base-branch: owl/<A>`.

Always point at the **immediate** predecessor, not the original ancestor. Git will transitively carry A's content through B into C's working tree. Skipping B and pointing C at A would bypass B's changes entirely when Owl sets up C's workspace.

### Fallback behavior and caveats

If the named base branch has already merged and been deleted on origin by the time the dependent plan runs, Owl silently falls back to `main`. This works correctly under the assumption that the ancestor's code was merged into `main` — which it is, for any normal merge flow. The dependent plan runs on `main` and never knows anything happened.

One subtlety: Owl treats "branch not on origin right now" as the fallback trigger. A branch that is still open on origin (merged but not deleted, or still under review) will be used as-is. If the ancestor's branch is merged-but-not-deleted, Owl will stack the dependent on the stale merged tip — that is the whole point of `base-branch` and is correct behavior, but the resulting PR shows a diff against the ancestor branch instead of `main`, which can look odd in review. Do not worry about this unless a reviewer asks.

### Common mistakes

1. **Skipping the wiring entirely** because "they run in filename order anyway." Queue order is not a dependency contract. If the ancestor fails or stalls, the dependent will run on a base that does not contain the expected code and will fail in a confusing way. Declare dependencies explicitly.
2. **Chaining low onto normal.** See the priority rule above.
3. **Pointing at the original ancestor in a chain instead of the immediate predecessor.** See the transitive chains rule.
4. **Using `base-branch` for every same-file edit.** Only for true code dependencies.

---

## Step 4 — Write the plan body

Owl agents have **no context from your session**. The plan must be fully self-contained. Write it as if handing it to a fresh agent who has never seen this conversation.

Required sections, in this order:

### 1. Context
Why this change is being made. What problem does it solve? What prompted it? Intended outcome.

### 2. Working directory
Explicit path(s) to the repo(s). For multi-repo plans, list all repos and note they must be modified together.

> Example: `server at <project-root>/project-api, mobile at <project-root>/project-web. Both repos must be modified together.`

Use `<project-root>` as a placeholder for the operator's project directory — never hardcode a home path like `/Users/.../` or `/home/.../` in the skill. The skill ships in the public Owl repo; examples should work for any operator, not just the author.

#### Edit-target path rule (LOAD-BEARING)

Owl executes plans inside a temporary git worktree, NOT in the original repo directory. The coder agent receives a runtime-interpolated WORKTREE CONTRACT block before your plan, telling it the worktree location and a prefix-translation rule. Your plan must cooperate with that contract:

- **Edit instructions in Sections 4 ("What to change") and 6 ("Files to modify") MUST reference files by repo-relative path**, either `<path-from-repo-root>` (when the plan is single-repo) or `<repo-name>/<path-from-repo-root>` (when the plan spans repos). Never embed an absolute path under the operator's project directory.
- **Anchors in Section 3 MAY use absolute paths**, because they are read-only grounding — Owl reads them before creating the worktree to verify they exist, and the agent treats them as identifiers, not edit targets. This is the only section where absolute paths are appropriate.

**Examples (placeholder-only, never real user paths):**

> Correct (Section 4 / 6): `pipeline/consolidation.py`, `project-api/pipeline/consolidation.py`
> Correct (Section 3 anchors): `<project-root>/project-api/pipeline/consolidation.py` — or use the same repo-relative form; both work.
> Wrong (Section 4 / 6): `<project-root>/project-api/pipeline/consolidation.py` ← absolute path in an edit target makes the agent edit the source copy, not the worktree copy, and Owl silently marks the plan "no changes".

A plan that violates this rule is the most common cause of the "Plan produced no changes in any repo" failure — Claude will follow the absolute path verbatim, edit the source repo, and Owl's worktree inspection will find nothing to commit.

Owl ships a standalone linter (`src/lint_plan.sh`) for this rule. Run it on your draft **before** dropping the plan into `owl/plan/` — it is a pre-queue author tool, never invoked by Owl at runtime. See Step 6b for the command and exit semantics.

### 3. Existing files to anchor on
File paths + approximate line numbers for code the plan depends on or will modify. Include short snippets when context matters. **Read the files before writing these — never guess line numbers.** Absolute paths are acceptable here — these are read before execution to verify the plan is grounded in real code.

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

**Note — Owl's deterministic test gate.** If the target repo has an
`OWL_TEST_CMD_<repo>` command configured in Owl's private `.env.local`, Owl
runs that suite at the top of every review round and feeds failures back to
the fix agent alongside the LLM reviewer feedback. You do NOT need to
instruct the agent to "run the tests" — it is automatic and non-negotiable.
What you DO need to do: list the same (or a superset of) commands in this
Verification section so human reviewers can reproduce them locally, and make
sure any new code paths the plan introduces are covered by tests that the
gate will actually exercise.

If the implementation/fix provider is Claude, Owl now reuses a per-plan Claude session across coder/fix rounds. That means plans benefit from keeping one coherent coding thread, while reviewers remain independent. You do not need to mention this in the plan body, but keep plans self-contained anyway because reviewers still see only the plan text plus the diffs.

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
- Anchors in Section 3 cite source files with line numbers (absolute paths OK here — they are read-only grounding).
- Edit instructions in Sections 4 and 6 use repo-relative paths, never absolute. See Step 2's edit-target path rule.
- Every referenced function/file was verified to exist (grep/read confirmed).

---

## Step 6b — Lint the draft before queueing

Owl ships a **standalone pre-queue linter** — a small bash script that enforces the Step 2 / Step 6 edit-target path rule. It is deliberately separate from Owl's runtime (`src/owl.sh`); Owl never invokes the linter during plan execution. Authors invoke it explicitly before moving a plan into `owl/plan/`.

```sh
cd /path/to/owl && ./src/lint_plan.sh path/to/your-draft-plan.md
```

Output:
- Exit 0 + `lint_plan: <path> — OK (0 edit-target path violations)` — plan passes.
- Exit 1 + a list of offending lines with column-aligned snippets — plan has violations; fix and re-run.

What the linter scans:
- Only Sections titled **"What to change"** and **"Files to modify"**.
- Ignores YAML frontmatter and fenced code blocks — snippet contents are never flagged.

What counts as a violation in those two sections:
- Any absolute filesystem path (anything starting with `/`).
- The placeholder `<project-root>/` — it's a documentation marker, not a valid edit target.

What counts as **OK**:
- Repo-relative paths like `pipeline/foo.py` or `<repo-name>/pipeline/foo.py`.
- Absolute paths in Section 3 anchors or any other section (those are read-only grounding and are intentionally exempt).

What the linter does NOT do:
- **It is not run by Owl during plan execution.** A malformed plan that skips the lint will still be executed; the runtime WORKTREE CONTRACT directive shipped in Owl and this lint are two independent defenses. The lint is the cheap, fast, local author check; the runtime directive is the agent-facing safeguard. Both exist for a reason.
- It does not call any LLM, does not touch git, and does not require the plan to be in `owl/plan/` yet.

Pass the linter. Then — and only then — move the file into `owl/plan/` per Step 8.

---

## Step 7 — Multi-repo / lockstep rules

- If the plan changes an **API contract**, server and mobile must ship together. State "Both repos must be modified together" near the top of the plan.
- If the plan touches **only one repo**, state so explicitly so reviewers don't hunt for missing changes.
- Owl commits each repo separately and opens separate PRs. It does NOT synchronize merge timing — that's the operator's job.

---

## Step 8 — Queue hygiene

- Drop the file into `owl/plan/` only.
- **Never** rename or renumber a plan already in `plan/` or `plan/done/`. If a number is wrong, leave it and pick the next free one for any fix.

### DO NOT commit plan files to the Owl repo

**Leave Owl's git tree alone.** The Owl repo's `.gitignore` ignores `plan/*.md` and `plan/done/` on purpose — plans are operator-local, ephemeral queue entries, not source material for the public Owl tool.

After writing a new plan file:

- **Do NOT run** `git add plan/NNN-*.md` in the `owl/` repo.
- **Do NOT run** `git add -f plan/NNN-*.md` to bypass the ignore rule. This is the exact mistake that has happened before — agents notice the file is ignored, assume that's a bug, and force-add past it.
- **Do NOT commit** anything in the `owl/` working tree as part of plan authoring. Your one and only deliverable is the file on disk at `owl/plan/NNN-<slug>.md`; Owl picks it up from the filesystem on its next cycle.

Why: Owl's queue lifecycle deletes each plan file from disk (`rm -f`) once it finishes executing — the completed copy lives at `plan/done/<name>_<timestamp>.done.md`. If a plan was force-tracked past the ignore rule, this later `rm` shows up as a permanent "deleted" entry in the operator's `git status` and pollutes every future commit. Don't create that debt.

If the user **explicitly** asks you to publish a specific plan to the Owl repo (e.g. "commit plan 092 as an example"), that's a one-off decision the user is making knowingly — do what they asked. But the default, and what this skill instructs, is: never touch git in the Owl repo.

Owl itself performs no `git` operations against its own repo. Its only `git` invocations are inside the target projects it executes plans against (project-api, project-web, etc.), scoped via `git -C "$repo_root"`.
