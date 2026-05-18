# agentprdiff Studio — five-minute quickstart

You don't need to write Python to use Studio. This walkthrough takes you from a fresh Docker install to your first regression catch.

> Looking for the dev-oriented `agentprdiff` CLI instead?
> See the top-level [README](../README.md) — Studio is the browser-based companion.

---

## 1 · Start Studio

You need Docker (any recent version). In a terminal:

```bash
cd studio
docker compose up --build
```

Wait about 30 seconds. When it says `Application startup complete`, open <http://localhost:8080>.

> **Placeholder:** *screenshot of the empty Projects page.*

You'll see "No projects yet" with a button to create one.

---

## 2 · Connect a project

Click **New project**. You'll see three intake modes:

| Mode | What it does | When to use it |
|---|---|---|
| **Git repository** | Studio clones the URL you paste | The agent code lives in a repo you can read |
| **Upload a zip** | Studio extracts the archive | The agent code is on your laptop and not in git yet |
| **HTTP endpoint** | Studio calls your deployed agent | The agent is already running somewhere; you don't have the code |

Pick the one that fits, fill in the name + URL/file, and click **Create project**.

> **Placeholder:** *screenshot of the wizard's three intake-mode cards.*

For **git** and **zip** projects, Studio walks the workspace looking for files that import `agentprdiff` and call `suite(...)`. If you don't have any suites yet, write one. The shape is on the [main README](https://github.com/vnageshwaran-de/agentprdiff#10-line-hello-world).

For **HTTP** projects, you author suites in JSON via the API (the Studio UI for this lands in a later release). See the example in the empty-state on the project detail page.

---

## 3 · Capture the first baseline

Once you have at least one suite, you'll see it on the project detail page. Each suite row has three buttons:

* **Record** — run the suite right now and save the resulting traces as the baselines.
* **Check** — run again and diff against the saved baselines. The CI command.
* **Review** — same diff as Check, but always exits clean. Built for local iteration.

Click **Record** on a suite to capture the first baseline.

> **Placeholder:** *screenshot of the suite row with the three buttons.*

You'll be taken to a live run page. Cases appear as they start, flip to green/red as they finish, and the whole run typically takes a few seconds. The first run on a project takes longer (Studio is installing dependencies into an isolated venv for that project).

When the run finishes, you'll see "succeeded" in green. The baselines are now saved.

---

## 4 · Catch a regression

Change something about your agent — swap the model, edit a prompt, change a tool. Push the change to the same branch, then hit **Sync** on the project detail page so Studio re-pulls the code. Then click **Check** on the same suite.

Watch the run page light up. If anything regressed, the run status flips to **regression** (orange) and at least one case shows up as orange in the grid.

> **Placeholder:** *screenshot of a run with one regressed case.*

Click the regressed case. You'll land on the diff viewer:

* **Stat strip** — cost, latency, and token deltas vs the baseline.
* **Assertions** — every grader's was→now verdict.
* **Tool sequence** — side-by-side list of which tools were called.
* **Output** — a colored diff of the agent's response text.

> **Placeholder:** *screenshot of the diff viewer.*

---

## 5 · Decide what to do

You have two paths:

* **The regression is a bug** — fix it. The next Check run goes back to green.
* **The new behavior is intentional** — click **Approve as new baseline** at the top of the case page. The trace from this run becomes the baseline going forward. For git and zip projects, the baseline JSON is also written to disk inside the project workspace, so a teammate using the CLI sees the same baseline.

That's the whole loop. Run, diff, decide.

---

## Where to put API keys

If your agent needs an `OPENAI_API_KEY` or similar, add it under **Secrets** in the top nav.

* Name: the env var your agent reads (e.g. `OPENAI_API_KEY`).
* Value: the actual key — Studio encrypts it at rest with a per-install Fernet key, and never returns the value via the API once stored.
* Scope: `global` (every project) or `project:<id>` (just one).

Studio injects these as environment variables when it spawns the run subprocess. To rotate, just save again with the same name + scope.

---

## When something goes wrong

The most common stumbles:

* **"No suites found yet"** on a fresh project → your suite file needs to call `suite(...)` and import from `agentprdiff`. The CLI quickstart shows the minimal shape.
* **HTTP project run says "limited trace mode"** → HTTP projects can only see the request input and the response body. They can't see model/tool calls inside your endpoint, so assertions like `tool_called(...)` will never fire. Use `contains`, `regex_match`, `latency_lt_ms`, etc.
* **"Couldn't approve baseline"** → check the project workspace exists on disk (git/zip mode). If Studio crashed mid-clone, hit Sync to re-pull.

Everything in the toast in the bottom-right tells you what to do next. Click the thumbs-down icon in the message log if a particular error message is unclear — those reports help us improve the copy.
