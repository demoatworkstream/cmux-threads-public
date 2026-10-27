# Design review gate

UI work isn't finished until it passes a design review by `⌂ Root · design-review`, one reviewer for all projects. The method, rubric, request template and review history live in `~/projects/design-review/` (see its README.md).

**Applies to** any thread that changes something a user sees: web pages, iOS/Android screens, emails, components, CSS. It's on by default. A project opts out with a line in its own AGENTS.md: `Design gate: off`. Backend, scripts and docs never go through it.

## Thread side (paste into the thread prompt)

Add this to "Start a thread", step 2, whenever the thread may change UI:

> **Design gate.** When your UI work is done and works, report `STATUS: ready for review` with: branch, URL or how to run it, the screens or flows you changed, and states worth checking (empty, error, long text). Don't report `finished` until the root sends you `Design: pass <review path>`. If the review fails, fix every blocker and major finding, then report `STATUS: ready for review` again with `Round: N` and the previous review path. Don't argue a finding by changing the rubric. If you think a finding is wrong, say so in your report and the reviewer will rule on it.

A thread with no UI change ends its finished report with `Design: n.a. (no UI change)`.

## Root side

1. **Request.** When a thread reports `STATUS: ready for review`:
   - rename its tab `◇ <topic>`;
   - write `~/projects/design-review/inbox/<project>-<YYYY-MM-DD>-<slug>.md` from the template in that folder's README (project, asked by, branch, URL, screens, states, round, previous review);
   - send the design-review root one line. Use SendMessage to `⌂ Root · design-review` when it's listed in ListAgents. Otherwise use `cmux send` to its tab (find it with `cmux tree --all`; check its input box and submit per [agents/claude.md](../agents/claude.md)). The line is: `Design review request from ⌂ Root · <project>: read ~/projects/design-review/inbox/<file>.md`
2. **Result.** The design-review root replies to your root with `Design: pass|fail <review path>` and the top findings. Read the review's `review.md` before forwarding anything.
   - `pass`: forward `Design: pass <path>` to the thread. It may now report `finished`.
   - `fail`: forward the path and the blocker and major findings. The thread fixes them and asks again (round 2, then 3). Keep the tab at `◇` while the review runs and `▶` while the thread fixes.
   - `fail` with `escalate: true` (round 3): mark the thread `?` and ask the user, through the Master if you're a project root: "Design review failed 3 rounds: <top findings>. Ship anyway? (pick one) 1. **Ship:** accept as is 2. **Fix:** one more round 3. **Drop:** stop this change". Record the answer in the request file. This is a product decision, not a matter of taste.
3. **Finished means passed.** A UI thread's `finished` must carry `Design: pass <review path>`, or `Design: shipped over fail <path>, user decided <date>`. Without it, keep the thread at `◇` and don't report it finished to the user.
4. The reviewer only reports. It never edits project code, so don't ask it to fix things.

## Reporting

- Relay a design `pass` together with the thread's finished news. Relay a `fail` as its verdict plus the top 3 findings. Relay an escalation as a decision for the user.
- The Master's cross-project status table gets an "in design review" column listing `◇` threads.
