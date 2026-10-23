# On-demand topology security review

This command uses the [Codex Security TypeScript SDK](https://learn.chatgpt.com/docs/security/sdk)
to review **declared** OT network settings. It discovers YAML configuration
under the selected dataset's `topology/`, `device_instances/`, and
`protocol_profiles/` folders, router configuration files under that testbed's
`router/` folder, and a selected JSON attack scenario catalog. New router files
are included automatically. It does not scan Docker application code,
contact GNS3, execute attacks, or verify the running network.

The SDK scan records evidence-backed findings and coverage. Its follow-up
produces a separate theoretical scenario for every cataloged attack family.
Those scenarios may use other plausible compromised hosts or command servers
than bundled lab nodes. A plausible path is not a
confirmed vulnerability; the reports mark blocked and uncertain steps.

## Set up and run

Use Node.js 24 or 26, Python 3.10 or newer, and an account with Codex Security
and **Daybreak Blue access**. Daybreak Blue is the minimum for this project's
security review. It is an access program, separate from the model ID. The
runner explicitly selects `daybreak_blue` for each scan and uses `gpt-6-sol`
by default. Choose another compatible model available to your approved account
or API project with `--model MODEL_ID`. The [OpenAI Daybreak guidance](https://learn.chatgpt.com/docs/cyber-safety)
explains access requirements; the [SDK guide](https://learn.chatgpt.com/docs/security/sdk)
documents model overrides. The repository's Python 3.12 environment meets the
Python requirement.
Install the pinned Node dependency from the repository root:

```bash
pnpm --dir scripts/security install --frozen-lockfile
```

Check the selected inputs and output location without starting a scan:

```bash
node scripts/security/run.mjs --preflight
```

If there is one dataset and one catalog, the command selects them automatically.
If there are several, choose the repository-relative paths explicitly:

```bash
node scripts/security/run.mjs --preflight --dataset testbeds/metropolis/datasets/water_treatment_v1 --catalog scripts/attacks/scenarios/v1.json
```

This prevents a new dataset or catalog from silently changing the scan's
meaning. Dataset and catalog choices are recorded in the results. Router files
with `.sh`, `.conf`, `.cfg`, `.yaml`, or `.yml` extensions are discovered
recursively under the selected testbed.

Start a manual review with an explicit estimated cost limit:

```bash
node scripts/security/run.mjs --max-cost-usd 5 --auth chatgpt --model gpt-6-sol
```

`chatgpt` is the default authentication choice and uses a file-backed Codex
sign-in. If that sign-in is unavailable, sign in to Codex first or use an
environment-provided `OPENAI_API_KEY` or `CODEX_API_KEY` with `--auth api-key`.
The chosen identity or API project must be approved for Daybreak Blue and have
access to the selected model. Selecting the program or model does not grant
access, and preflight does not verify entitlement. Never put a key in the
command line or a tracked file. The cost limit is an estimate; requests already
running can finish slightly above it. A failed or interrupted scan retains its
available output and reports an incomplete review.

Pass `--output-root /path/outside/this/repository` to choose a different
results root. By default, each run is saved under the sibling directory
`../metropolis-security-results/<UTC-time>_<Git-short-SHA>/`. The SDK requires
its output directory to be outside the scanned Git worktree. Results can
contain source excerpts and security details; restrict access to that folder.

## Results

Each run contains:

| File | Meaning |
|---|---|
| `sdk/report.md`, `sdk/findings.json`, `sdk/coverage.json`, `sdk/scan-manifest.json` | SDK findings and reviewed/deferred scope |
| `scenarios.md` | Human-readable table and theoretical attack paths |
| `scenarios.json` | Structured theoretical paths for later analysis |
| `review-metadata.json` | Git revision and dirty state, hashes of selected inputs and prompts, selected model and Daybreak Blue program, UTC times, output paths, failures, and completion status |

The runner validates family coverage, basic JSON structure, selected evidence
paths, UTC time, and the Markdown table. It marks scenarios incomplete when the
SDK follow-up fails or writes invalid files, even if the security scan itself
completed. A completed SDK scan can still have deferred areas in `coverage.json`.
The runner does not independently prove that a cited CVE applies; the review
instructions require product/version evidence and an authoritative advisory.

Run the local mocked tests without authentication or GNS3:

```bash
node --test --test-isolation=none scripts/security/review.test.mjs
```

The output is for offline review of the repository's intended settings. The
user verifies effective cabling, routes, firewall behavior, and traffic in
their own GNS3 environment.
