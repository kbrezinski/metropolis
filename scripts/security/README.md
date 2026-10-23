# On-demand topology security review

Review the declared network and generate theoretical attack scenarios using the
[Codex Security SDK](https://learn.chatgpt.com/docs/security/sdk).
This command reads configuration; it does not contact GNS3 or execute attacks.

Selected inputs:

- YAML under the dataset's `topology/`, `device_instances/`, and
  `protocol_profiles/`.
- Router files discovered under the testbed's `router/` directory.
- One versioned JSON catalog from `scripts/attacks/scenarios/`.

The SDK produces evidence-supported findings. A follow-up produces one
**theoretical** scenario per catalog family, with permitted, blocked, and
uncertain paths. Bundled bot/C2 nodes are examples, not required roles.
Attack binaries and Docker application code are outside the review inputs.

## Set up and run

From the repository root, use Node.js 24 or 26, Python 3.10+, and an account
with Codex Security and Daybreak Blue access. The repository's Python 3.12
environment meets the Python requirement.

### 1. Install the Node dependency

```bash
pnpm --dir scripts/security install --frozen-lockfile
```

### 2. Check inputs without starting a scan

```bash
node scripts/security/run.mjs --preflight
```

One dataset and one catalog are selected automatically. With multiple choices,
pass `--dataset PATH --catalog PATH`; paths are repository-relative.
New router files are discovered automatically.

### 3. Run with a chosen budget

```bash
node scripts/security/run.mjs --max-cost-usd 5
```

The example sets a USD 5 estimated limit; choose your own amount. In-flight
requests may finish above that estimate.

| Setting | Default / override |
|---|---|
| Authentication | Codex ChatGPT sign-in; `--auth api-key` uses environment-provided `OPENAI_API_KEY` or `CODEX_API_KEY` |
| Model | `gpt-6-sol`; override with `--model MODEL_ID` |
| Access program | Runner selects `daybreak_blue`; requires an approved identity/project and model access |
| Results root | Sibling `../metropolis-security-results/`; override with `--output-root PATH` |
| Run folder | UTC time and Git short revision |

Results must be **outside the Git worktree**. Preflight does not verify
authentication or access entitlement. Keep keys in the environment.
See [OpenAI's Daybreak guidance](https://learn.chatgpt.com/docs/cyber-safety)
for current access requirements.

## Results

| File | Contents |
|---|---|
| `sdk/report.md`, `sdk/findings.json` | SDK findings |
| `sdk/coverage.json`, `sdk/scan-manifest.json` | Reviewed/deferred scope and SDK run details |
| `scenarios.md`, `scenarios.json` | Theoretical scenario table and structured paths |
| `review-metadata.json` | Selected inputs/prompts and hashes, Git state, model/program, UTC times, paths, failures, completion status |

Incomplete output is retained and marked failed or incomplete. Scenario
validation checks family coverage, JSON shape, evidence paths, UTC time, and
the Markdown table. CVEs require product/version evidence and an authoritative
advisory; the runner does not independently establish that a CVE applies.
Live network behavior still requires the user's GNS3 verification.

For mocked local tests:

```bash
node --test --test-isolation=none scripts/security/review.test.mjs
```
