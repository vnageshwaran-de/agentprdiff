# Screencast script — 75 seconds

Target audience: someone who's heard of agentprdiff but hasn't used Studio.

The whole pitch: **"Click record. Change your agent. Click check. See the diff. Approve if it's intentional."** That's it.

Aim for a clean voiceover, no background music, no cuts mid-action. Each scene is one focus.

---

## Scene 1 (0:00–0:10) — Hook

**Visual.** Browser, full-screen. The Projects page is empty.

**VO:** "Your LLM agent has behaviors you rely on — a specific tool gets called, a refund amount is quoted, latency stays under budget. When you upgrade a model or tweak a prompt, those behaviors drift. agentprdiff Studio catches that drift before the PR merges."

---

## Scene 2 (0:10–0:25) — Connect a project

**Visual.** Click **New project**. Show the three intake-mode cards. Click **Git repository**. Paste a URL. Click **Create project**.

**VO:** "Point Studio at a repo, a zip, or an HTTP endpoint. It clones the code, walks it for suites, and shows you what it found."

After Create lands on project detail. Pause briefly on the suite row.

---

## Scene 3 (0:25–0:40) — Record the first baseline

**Visual.** Hover over the **Record** button — show the tooltip. Click it.

**VO:** "Record runs every case and saves the trace as the baseline. This is the snapshot you're guarding."

Live run page. Cases flip green one by one. Status pill turns "succeeded."

---

## Scene 4 (0:40–0:55) — Trigger a regression

**Visual.** Cut to a terminal: change one line of the agent (e.g. `model="gpt-4o" → "gpt-4o-mini"`). Push. Back in the browser, click **Sync**, then **Check** on the same suite.

**VO:** "Now swap the model. Sync. Check. The run page lights up live as Studio re-runs every case."

The case grid flips: most stay green, one turns orange.

---

## Scene 5 (0:55–1:10) — The diff

**Visual.** Click the regressed case. The diff viewer loads. Pan slowly through the assertion table, then the colored output diff.

**VO:** "One case regressed. Studio shows you exactly what changed — every assertion's was-to-now verdict, the cost and latency delta, the tool sequence, the response text."

---

## Scene 6 (1:10–1:15) — Decide

**Visual.** Click **Approve as new baseline**. The toast appears: "Approved baseline v2."

**VO:** "If the change is intentional, one click promotes the new trace to be the baseline. If not, you fix it and re-run. That's the whole loop."

Fade to logo + URL.

---

## Recording notes

* OS: any with retina display, browser at 1440×900 (recording target 1080p).
* Theme: light. The diff colors (green/red) read clearer than dark for a first-time viewer.
* Cursor: highlighted (use a screen-recording tool's cursor magnifier).
* No music. Quiet voiceover, mono, light compression.
* Use the [Quickstart](quickstart-for-non-devs.md)'s example repo so the recorded URL matches what a viewer can clone.

## Open questions for the editor

* Should we include captions for the voiceover? (Yes for social autoplay.)
* Should Scene 4's "change the agent" happen in-browser instead of a terminal cut? (Maybe in the next version — for now the terminal is honest about what a vibecoder will need to do.)
