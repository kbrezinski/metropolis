<!--
INSTRUCTIONS FOR THE NEXT AGENT

1. This is a living bug/cleanup list, not a report to archive. Read it before
   changing code, and keep it open while you work.
2. Work from the top down. P0 items block CI or a safe lab run.
   Do not start P2 documentation cleanup while a P0 is open.
3. A checkbox means "not fixed". Mark it [x] only after the fix is committed
   AND the "Verify" step has been run. Add a short result under the item.
   Never delete a completed item: the history is useful.
4. Add new findings at the bottom of the "Running issue list", using the next
   free REV-xxx id. Do not renumber existing items. Record evidence
   (command output or file:line) and a concrete verification step.
5. Line numbers were accurate at the timestamp below. Re-check them before
   editing; files may have moved.
6. The review was done on Windows under the DSH workspace-write sandbox. That
   sandbox denied access to pytest tmp_path/TemporaryDirectory directories
   with WinError 5. Do NOT treat those permission errors as repository bugs.
   Re-run the full suite outside that sandbox before drawing conclusions.
7. Two items depend on a product decision, not just code: the uncommitted
   deletion of main.py and whether paper/main.pdf should be tracked. Resolve
   those with the maintainer before changing docs or ignore rules.
8. Keep AGENTS.md and CLAUDE.md in step. Both are "near-copies" by policy.
9. When an item is fixed, update the "Last reviewed" timestamp and add the
   verifying command/result to the "What was verified" table.
-->

# Repository review — bug and cleanup TODO

**Last reviewed:** 2026-10-21T16:47:27+11:00 (Australia/Sydney)
**Scope:** `src/metropolis`, `tests/`, `scripts/`, `testbeds/metropolis`, `docs/`, CI config, and packaging.
**Working tree at review:** `M .gitignore`, `D main.py` (both uncommitted; see REV-030 and REV-031).
**Line references** are from this checkout and may drift.

## What was verified

| Check | Result | Notes |
|---|---|---|
| `python scripts/validate_metropolis_topology.py` | **PASS 10/10** | Schemas, address plan, inventory, routers, static routes, switches, links all cross-check. |
| `python scripts/generate_switch_reference.py --check` | **PASS** | `testbeds/metropolis/switch/README.md` is up to date. |
| `ruff check .` | **PASS** | No lint findings. |
| `uv run ruff format --check .` | **FAIL** | One file would be reformatted: `testbeds/metropolis/devices/field/sensors/coap_sensor.py`. See REV-002. |
| `codespell` on tracked files | **FAIL (4)** | Two misspellings of *retrieving* and two of *unparsable*. See REV-003/REV-004. |
| `pytest` collection | **504 tests / 34 files** | The README's "313 tests / twenty-one files" is stale. |
| Full `pytest` run | **INCONCLUSIVE** | `tmp_path` and `TemporaryDirectory` failed with `PermissionError: [WinError 5]` under the sandbox used for the review. Every observed `E`/`F` traced to that failure, not to a code assertion. Re-run on a normal desktop/CI runner. |
| `python -m metropolis.topology check` from source | **PASS** | "No contradictions found." |
| `python scripts/build_a_topology.py --break-it` | **Exit 1, expected** | Reported the two intended subnet/gateway problems. |
| Invalid-subnet validation crash | **REPRODUCED** | `Topology(...Device(subnet="not-a-cidr", address="10.0.0.1/24"))` raises `ValueError` instead of returning a problem. See REV-007. |
| Planned-link DOT output | **REPRODUCED** | Produces `[style="dashed" label="..."]` (missing comma), invalid Graphviz. See REV-008. |
| `topology check --out FILE` | **REPRODUCED** | Option is accepted, but FILE is not created. See REV-009. |
| Wheel packaging | **PASS** | The built wheel contains all four dataset YAML documents. Installed into a separate target, `import metropolis` resolved there, `load_bundled_topology().problems()` was empty, and `python -m metropolis.topology check` exited 0. `tests/test_packaging.py` exercises the same flow; its pytest invocation is blocked here only by the sandbox tmp-directory error, but the test function itself passed when called with a workspace path. |

### Caveat on the test count

`pytest --collect-only -q` reports 504 collected tests. The exact count can change
as tests are added; do not hard-code 504 in user-facing docs. Use "the suite"
or a generated badge instead.

---

## Running issue list

Append new items at the end with the next free id. Each item should be actionable
on its own. Use `[x]` only after verification.

### P0 — CI and safe-lab blockers

- [ ] **REV-002 — `ruff format --check` fails on the CoAP sensor.**
  - Evidence:
    [coap_sensor.py](../testbeds/metropolis/devices/field/sensors/coap_sensor.py#L197-L200)
    (the `b"/".join(...)` generator is split differently from Ruff's output).
  - Impact: CI's format step fails before tests run.
  - Fix: `uv run ruff format .`
  - Verify: `uv run ruff format --check .` exits 0.

- [ ] **REV-003 — Two tracked Dockerfiles misspell "retrieving".**
  - Evidence:
    [Dockerfile.builder.template:50](../scripts/attacks/mirai/Dockerfile.builder.template#L50)
    and
    [Dockerfile.experimentation:65](../scripts/attacks/mirai/Dockerfile.experimentation#L65).
  - Impact: the spelling workflow fails on every push.
  - Fix: correct the comments to "retrieving".
  - Verify: `uv run codespell . --skip="./.git,./.venv,./dist,./build,./uv.lock"` exits 0.

- [ ] **REV-004 — One tracked test file uses a spelling codespell rejects.**
  - Evidence:
    [test_gns3_device_logs.py:77](../tests/test_gns3_device_logs.py#L77-L87).
  - Impact: the spelling workflow fails. The test string is flagged; the
    dictionary spelling is `unparsable`.
  - Fix: change the test strings (and any assertions comparing them) to
    `unparsable`, or add the flagged string to `[tool.codespell]` deliberately.
  - Verify: same codespell command as REV-003.

- [ ] **REV-005 — A missing image template silently drops nodes and can delete live ones.**
  - Evidence:
    `_device_nodes()`
    ([scripts/gns3/topology.py](../scripts/gns3/topology.py#L104-L110)) appends
    to `missing_templates` and continues. `builder.build()` forwards only
    `topology.skipped`
    ([scripts/gns3/builder.py](../scripts/gns3/builder.py#L169)), and the guard
    in
    [scripts/gns3/build_topology.py](../scripts/gns3/build_topology.py#L116-L125)
    iterates `topology.nodes`, which already excludes the dropped node. The
    final return only checks switch/router failures
    ([build_topology.py](../scripts/gns3/build_topology.py#L196-L197)).
  - Impact: `build_topology` can exit 0 with a node missing. With
    `--prune-missing`, the existing node is then deleted because it is not in
    the truncated plan
    ([builder.py](../scripts/gns3/builder.py#L189-L194)).
  - Fix: fail before building if `topology.missing_templates` is non-empty, and
    never include such nodes in the prune target set.
  - Verify: unit test with an inventory device whose image is absent from
    `TEMPLATES`; assert `build_topology` returns non-zero and does not call
    `delete_node`.

- [ ] **REV-006 — Router configuration does not wait for each command.**
  - Evidence:
    `Console.expect()` searches the buffer passed in
    ([router_config.py](../scripts/gns3/router_config.py#L140-L160));
    `apply_router_config()` passes the previous buffer, which already ends in
    `CONFIG_PROMPT`, into the next `expect`
    ([router_config.py](../scripts/gns3/router_config.py#L198-L204)).
  - Impact: every `expect` returns immediately, commands are sent
    back-to-back, and `run show interfaces` can stop on an earlier buffered
    prompt. This can report addresses as missing when they are configured.
  - Fix: consume the prompt before sending the next command, or pass an empty
    buffer and read the new prompt explicitly; make the final read wait for the
    command echo plus the prompt.
  - Verify: a fake console that emits prompts in separate chunks (not one
    `recv`) must show commands waiting for their own prompts. Add a negative
    test where a prompt never arrives.

### P1 — correctness and robustness

- [ ] **REV-007 — `Topology.problems()` crashes on a malformed device subnet.**
  - Evidence:
    [topology/__init__.py](../src/metropolis/topology/__init__.py#L1116) calls
    `ipaddress.ip_network(device.subnet, strict=False)` without a `try`, even
    though the address parsing above it is guarded.
  - Impact: validation raises `ValueError` instead of returning a readable
    problem. Reproduced with a device whose subnet is `not-a-cidr`.
  - Fix: wrap the subnet parse and append a problem message.
  - Verify: add a test asserting `problems()` returns a message and does not
    raise for an invalid subnet.

- [ ] **REV-008 — `to_dot()` emits invalid DOT for planned links.**
  - Evidence:
    [topology/__init__.py](../src/metropolis/topology/__init__.py#L1587) builds
    `[style="dashed" label="..."]`, missing the comma between attributes.
  - Impact: Graphviz rejects the output. The dataset currently has no
    `status: planned`, so the default render hides the bug; the docs describe
    links as planned.
  - Fix: emit `[style="dashed", label="..."]`.
  - Verify: add a test with `Link(status="planned")`; run the DOT through
    `dot` (or assert the comma).
  - Note: also escape backslashes/newlines in labels if arbitrary names are
    allowed.

- [ ] **REV-009 — `topology check --out FILE` is ignored.**
  - Evidence: the option is defined at
    [topology/__main__.py](../src/metropolis/topology/__main__.py#L158), but
    `_cmd_check()` never reads `args.out`
    ([topology/__main__.py](../src/metropolis/topology/__main__.py#L76-L88)).
  - Impact: the advertised option silently does nothing; no file is created.
  - Fix: write the summary (and problems) to the file, or remove the option.
    Prefer `_write_or_print`.
  - Verify: `check --out .tmp/check.txt` creates the file and exits 0; add a
    CLI test.

- [ ] **REV-010 — Mirai choreography hard-codes bot addresses.**
  - Evidence:
    [run_mirai_choreography.py:223](../scripts/attacks/mirai/run_mirai_choreography.py#L223)
    builds `10.99.10.{100 + index}` instead of reading the selected inventory.
  - Impact: custom datasets are ignored. A custom `MIRAI-BOT-A` at
    `10.77.4.13` is reported at `10.99.10.100`, contradicting the
    inventory-driven contract in
    [scripts/attacks/README.md](../scripts/attacks/README.md#L8).
  - Fix: resolve bot nodes by role/name from the inventory, preserving the
    bundled preferred roles and allowing explicit selection.
  - Verify: extend `test_attack_custom_dataset.py` to assert the custom bot
    address in a dry run.

- [ ] **REV-011 — `--host` alone reuses another node's port and credentials.**
  - Evidence:
    [_common.py](../scripts/attacks/_common.py#L275-L294) creates the
    credential-free synthetic target only when both `--host` and `--port` are
    given and `--node` is not. `--host 10.20.23.99` alone selects the default
    broker, then overrides only the host.
  - Impact: credentials and service settings from
    `MET-MQTT-BROKER-01` are sent to a different appliance, contrary to
    [scripts/attacks/README.md](../scripts/attacks/README.md#L74-L79).
  - Fix: treat any `--host` without `--node` as an explicit target; require
    `--port` (or use the protocol default) and do not inherit credentials.
  - Verify: test `--host` alone, with a dry run and with a mocked connection,
    and assert the selected credentials are empty.

- [ ] **REV-012 — Inventory validation accepts records that later crash.**
  - Evidence:
    [_common.inventory()](../scripts/attacks/_common.py#L150-L174) validates
    names and protocol/environment shape but not `address`, and does not
    normalize a missing `environment`. Consumers index `device["address"]`
    ([_common.py](../scripts/attacks/_common.py#L300),
    [_common.py](../scripts/attacks/_common.py#L398)) and
    `device["environment"]`
    ([mqtt_flood_attack.py:42](../scripts/attacks/mqtt/mqtt_flood_attack.py#L42)).
    `cli()` does not catch `KeyError`
    ([_common.py](../scripts/attacks/_common.py#L510)).
  - Impact: a minimal custom inventory produces a traceback instead of a
    controlled exit 2.
  - Fix: require a private IPv4 `address`, default `environment` to `{}`, and
    catch `KeyError` in `cli()` (or avoid direct indexing).
  - Verify: custom-inventory tests that omit `address` and `environment`; assert
    exit 2 and a JSON error line.

- [ ] **REV-013 — Startup tiers are not dependency barriers.**
  - Evidence:
    [lifecycle.py](../scripts/gns3/lifecycle.py#L132-L148) issues every
    `start_node` in tier order, then calls `wait_until_running` once at the end.
  - Impact: clients start while routers/switches are still booting, defeating
    the retry protection described in the module docstring and
    [scripts/gns3/README.md](../scripts/gns3/README.md).
  - Fix: wait for each tier (or at least for routers/switches) before starting
    the next.
  - Verify: fake client records the order: no higher-tier start until the lower
    tier reports started.

- [ ] **REV-014 — Re-running the builder does not reconcile existing nodes.**
  - Evidence:
    [builder.py](../scripts/gns3/builder.py#L54-L79) reuses a node by name
    without checking template/image/compute and applies the environment only
    on create.
  - Impact: changed `NODE_IP`/`NODE_GATEWAY` values or a wrong image silently
    persist, so the server does not "match" the declaration.
  - Fix: compare the existing node's template/compute and update environment
    (and optionally the template) on reuse; report differences.
  - Verify: test reuse with changed environment and with a wrong template.

- [ ] **REV-015 — `build_topology` exit status under-reports partial builds.**
  - Evidence:
    `result.skipped` is printed but not counted
    ([build_topology.py](../scripts/gns3/build_topology.py#L136-L146)), and
    router address mismatches print but do not fail
    ([build_topology.py](../scripts/gns3/build_topology.py#L176-L181)). Only
    switch verification failures and router exceptions return 1
    ([build_topology.py](../scripts/gns3/build_topology.py#L196-L197)).
  - Impact: a partial lab can return success to scripts/CI.
  - Fix: count skipped links/nodes and missing router addresses as failures, or
    add a strict mode.
  - Verify: CLI tests for each failure class.

- [ ] **REV-016 — `client.node()`/`client.link()` do not reliably return `None` for 404.**
  - Evidence:
    [client.py](../scripts/gns3/client.py#L192-L198) and
    [client.py](../scripts/gns3/client.py#L280-L286) allow 404, but
    [_transport.py](../scripts/gns3/_transport.py#L95-L104) returns the decoded
    404 JSON error body.
  - Impact: callers receive a truthy error dict instead of "not found"; tests
    fake `None` directly and miss the transport behaviour.
  - Fix: map allowed 404 to `None` in the transport, or return `None` in the
    client when the response is an error object.
  - Verify: transport-level test with a real 404 JSON body.

- [ ] **REV-017 — GNS3 config version selection is lexicographic.**
  - Evidence:
    [config.py](../scripts/gns3/config.py#L50-L54) sorts by directory name.
    `2.10` sorts below `2.9`.
  - Impact: an older config is selected after a GNS3 upgrade.
  - Fix: parse versions numerically, or use the highest version tuple.
  - Verify: test with `2.2`, `2.9`, `2.10`.

- [ ] **REV-018 — `lab_lifecycle --dataset` is ignored.**
  - Evidence: the option is parsed at
    [lab_lifecycle.py](../scripts/gns3/lab_lifecycle.py#L60-L67), but
    `load_plan()` is called with defaults.
  - Impact: users can point at a custom dataset and silently get the default.
  - Fix: pass the dataset path through to `load_plan`, or remove the option.
  - Verify: CLI test with a temporary dataset; assert it is used.

- [ ] **REV-019 — Capture preflight is inconsistent.**
  - Evidence:
    `experiment.run_experiment` fails on every unresolved declared link even
    when `--links` selected a subset
    ([experiment.py](../scripts/gns3/experiment.py#L138-L142));
    `capture_traffic --all-built` includes synthetic `gns3_*` statuses in
    preflight
    ([capture_traffic.py](../scripts/gns3/capture_traffic.py#L84-L90)).
  - Impact: a good selective capture can fail; a suspended/already-capturing
    undeclared physical link can fail an otherwise successful run.
  - Fix: filter preflight to the chosen links and to declared IDs; treat
    synthetic statuses as informational.
  - Verify: tests for selected-subset and suspended-undeclared-link cases.

- [ ] **REV-020 — Modbus timing flags are silently ignored; `freeze-heartbeat` is not a freeze.**
  - Evidence:
    [modbus_manipulation_attack.py](../scripts/attacks/modbus/modbus_manipulation_attack.py#L102-L106)
    uses `cycles=[0]` for every non-heartbeat action.
  - Impact: `--duration`, `--period`, and `--max-messages` do nothing for
    set-level/set-flow/set-quality/force-pump/release-pump. `freeze-heartbeat`
    is behaviourally identical to `overwrite-heartbeat` (both repeatedly write
    0) and cannot freeze the PLC timer.
  - Fix: use the schedule for all actions, or reject the timing flags with a
    clear error for one-shot actions. Implement a real heartbeat freeze if the
    name stays.
  - Verify: tests asserting the number of writes and the heartbeat behaviour.

- [ ] **REV-021 — Merlin options and upload handling are inconsistent.**
  - Evidence:
    `--listener-scheme` is defined at
    [run_merlin.py:61](../scripts/attacks/merlin/run_merlin.py#L61), but the
    console always sends `use https` at
    [run_merlin.py:126](../scripts/attacks/merlin/run_merlin.py#L126). A
    malformed `POST /upload/` path raises `IndexError` at
    [start_merlin_cnc.py:367](../testbeds/metropolis/devices/attack/merlin/cnc/start_merlin_cnc.py#L367),
    outside the handler's `except` tuple.
  - Impact: the JSONL claims a scheme the listener does not use; a malformed
    request kills a handler thread without a response.
  - Fix: send the requested scheme (or reject non-https), validate the path
    length, and add `IndexError` to the handled exceptions.
  - Verify: test `--listener-scheme http` and `POST /upload/ HTTP/1.0`.

- [ ] **REV-022 — Broker TLS config is appended on every start.**
  - Evidence:
    [broker-entrypoint.sh](../testbeds/metropolis/devices/services/mqtt_broker/broker-entrypoint.sh#L45-L54)
    uses `cat >> mosquitto.conf`.
  - Impact: restarting a TLS-enabled broker in the same writable layer
    duplicates `listener 8883`, `cafile`, `certfile`, and `keyfile`; Mosquitto
    can fail to start.
  - Fix: write an idempotent config, replace a marked block, or generate the
    file from a template.
  - Verify: start-container test that runs the entrypoint twice and checks for
    one listener block.

- [ ] **REV-023 — Half-configured client TLS is accepted.**
  - Evidence:
    [tls_config.py](../testbeds/metropolis/devices/runtime/tls_config.py#L71-L79)
    validates only the CA. A client certificate without its key, or
    nonexistent cert/key paths, passes startup.
  - Impact: the failure moves to connect time, where it is harder to diagnose.
    This contradicts the module docstring's claim to refuse half-configured TLS.
  - Fix: require cert+key together and check both files exist.
  - Verify: tests for cert-only, key-only, and missing paths.

- [ ] **REV-024 — `pytest` is a runtime dependency.**
  - Evidence: [pyproject.toml](../pyproject.toml#L11-L18) lists
    `pytest>=9.0.3` in `[project].dependencies`; it is also in the `dev` group.
  - Impact: installing the library pulls the test framework into production
    environments.
  - Fix: remove `pytest` from runtime dependencies; keep it in `dev`.
  - Verify: inspect the built wheel metadata, or `pip show` in a clean install.

- [ ] **REV-025 — GNS3 CLI flags expose passwords in process arguments.**
  - Evidence:
    `--password` and `--router-password`
    ([scripts/gns3/_cli.py](../scripts/gns3/_cli.py#L66-L78),
    [scripts/gns3/build_topology.py](../scripts/gns3/build_topology.py#L81-L86))
    accept secrets on the command line, while `experiment_run` supports
    `METROPOLIS_ROUTER_PASSWORD`.
  - Impact: secrets appear in shell history and process listings.
  - Fix: prefer environment variables or an interactive prompt; keep the flags
    for compatibility but document the exposure.
  - Verify: `--help` and a process-args test; document the environment path.

### P2 — documentation, housekeeping, and smaller findings

- [ ] **REV-026 — Root README says the GNS3 builder is future work/manual.**
  - Evidence: [README.md](../README.md#L226-L232) says templates, topology,
    router startup, and switch setup are "all manual" and the builder is future
    work. The builder is implemented and documented in
    [scripts/gns3/README.md](../scripts/gns3/README.md). The same claim appears
    in [scripts/README.md](../scripts/README.md#L6-L12).
  - Fix: remove the stale paragraphs and point at `scripts/gns3/README.md`.

- [ ] **REV-027 — README links point into the git-ignored `examples/` directory.**
  - Evidence: [README.md](../README.md#L77) and
    [README.md](../README.md#L328) point at `examples/build_a_topology.py` and
    `examples/test_metropolis_import.ipynb`. The tracked files are
    [scripts/build_a_topology.py](../scripts/build_a_topology.py) and
    [scripts/test_metropolis_import.ipynb](../scripts/test_metropolis_import.ipynb).
    `.gitignore` ignores `examples/`, so the links 404 in a fresh clone.
  - Fix: update the links to `scripts/`, and update
    [docs/understanding-the-repo.md](understanding-the-repo.md#L480) too.

- [ ] **REV-028 — README status counts are stale.**
  - Evidence: [README.md](../README.md#L45) says four JSON schemas (there are
    six); [README.md](../README.md#L44) says ten access segments (the dataset
    has thirteen); [README.md](../README.md#L53) says 313 tests (504 are
    collected); [README.md](../README.md#L58) says "this has three" device
    families (the device guide lists fifteen images).
  - Fix: correct the counts or remove hard-coded numbers that will rot.

- [ ] **REV-029 — README describes the wrong switch and capture behaviour.**
  - Evidence: [README.md](../README.md#L198-L200) says to import an Ethernet
    switch appliance; switches are repo-owned Docker templates. The capture
    section says files are written next to the GNS3 project
    ([README.md](../README.md#L247)); `capture.py` copies verified files into
    `datasets/.../captures/<run-id>/`.
  - Fix: align the instructions with
    [scripts/gns3/README.md](../scripts/gns3/README.md) and `capture.py`.

- [ ] **REV-030 — `main.py` is deleted in the working tree but still documented.**
  - Evidence: `git status` shows `D main.py`; [AGENTS.md](../AGENTS.md#L9),
    [CLAUDE.md](../CLAUDE.md#L9), and
    [docs/understanding-the-repo.md](understanding-the-repo.md#L149) still
    describe the hello-world stub.
  - Decision needed: restore `main.py` or remove it from all docs. The package
    does not need it.

- [ ] **REV-031 — The new `paper/` ignore rule cannot include `paper/main.pdf`.**
  - Evidence: [.gitignore](../.gitignore#L17-L20) adds `paper/` and then
    `!paper/main.pdf`; `*.pdf` is ignored earlier. Git cannot descend into an
    ignored parent directory.
  - Impact: if the intent is to track the paper PDF, it will not be tracked.
  - Fix: use `paper/*` + `!paper/main.pdf` (and resolve the broad `*.pdf` rule),
    or drop the negation. Use `git add -f` only as a one-off, not a policy.

- [ ] **REV-032 — The plain-language guide repeats stale facts.**
  - Evidence:
    [docs/understanding-the-repo.md](understanding-the-repo.md#L461) says
    "twenty-one files / 313 tests";
    [docs/understanding-the-repo.md](understanding-the-repo.md#L135-L149)
    describes `examples/` and `main.py`; section 9.7 still uses
    `examples/build_a_topology.py`
    ([docs/understanding-the-repo.md](understanding-the-repo.md#L1062-L1068)).
  - Fix: one documentation reconciliation pass across `README.md`,
    `scripts/README.md`, this guide, `AGENTS.md`, and `CLAUDE.md`.

- [ ] **REV-033 — AGENTS.md/CLAUDE.md are stale and must stay in step.**
  - Evidence: both still mention `main.py` and
    `examples/build_a_topology.py`
    ([AGENTS.md](../AGENTS.md#L9),
    [AGENTS.md](../AGENTS.md#L29),
    [CLAUDE.md](../CLAUDE.md#L9),
    [CLAUDE.md](../CLAUDE.md#L29)).
  - Fix: update both together; they are intentionally near-copies.

- [ ] **REV-034 — Device TLS tests grep source instead of exercising behaviour.**
  - Evidence: `test_device_tls.py` has several tests that read files and assert
    strings are present, rather than starting a client and applying TLS.
  - Impact: a device can regress while the string remains.
  - Fix: add behavioural tests for at least one MQTT client and the broker
    listener; keep the grep tests as cheap packaging checks.

- [ ] **REV-035 — Dead code and minor attack inconsistencies.**
  - Evidence:
    - [`_external_setup.py`](../scripts/attacks/_external_setup.py) is never
      imported or discovered.
    - [`hping3_attack.py:77`](../scripts/attacks/hping3/hping3_attack.py#L77)
      `syn_probe` is unused.
    - `coap_sensor.readings()` is unused.
    - `start_bot.py:52` advertises `ip-port`, but the parser only splits on
      `:` at line 57, so `10.99.10.1-23` raises `ValueError`.
    - `start_merlin_agent.py:52-62` accepts `https://` but speaks plain HTTP.
    - `mqtt_sensor.py:290-292` assigns percent/unit-per-second units to
      pressure, temperature, turbidity, and pump readings.
    - `start_cnc.py:120-138` claims to handle command polls but only handles
      `CHECKIN`/`CLEAR`, and creates two unclosed `makefile` objects.
    - `coap_experiment_report` has no `--dry-run`, although
      [scripts/attacks/README.md](../scripts/attacks/README.md#L23) says every
      attack command does.
  - Fix: delete or wire up dead code; correct the docstrings/units; decide
    whether offline reports need `--dry-run`.

- [ ] **REV-036 — Small GNS3 input-validation gaps.**
  - Evidence:
    - A declared switch endpoint without `port_role` maps to adapter 0
      ([topology.py](../scripts/gns3/topology.py#L206-L215)) but is omitted from
      switch configuration
      ([topology.py](../scripts/gns3/topology.py#L169-L172)).
    - An unknown role raises `KeyError`; `eth0.vif10` raises `ValueError` in
      `_adapter_for`.
    - [`switch_config.py`](../scripts/gns3/switch_config.py#L28-L30) indexes
      `uplinks[0].vlans[0]` before checking that there is one VLAN; segment
      membership uses value equality (`link in uplinks`).
    - `ConfigParser()` interpolation can fail on a password containing `%`
      ([config.py](../scripts/gns3/config.py#L59-L63)).
    - `_transport` catches `URLError` but not `TimeoutError`/other `OSError`;
      `client.reachable()` catches only `Gns3Error`.
    - `environment_string()` does not escape quotes/newlines
      ([topology.py](../scripts/gns3/topology.py#L96)).
  - Fix: validate earlier, use `interpolation=None`, broaden the transport
    error handling, and escape environment values.

### Test gaps to close as fixes land

- [ ] **REV-T02 — `to_dot()` with `status="planned"`** (REV-008).
- [ ] **REV-T03 — `topology check --out`** (REV-009).
- [ ] **REV-T04 — Invalid device subnet does not raise** (REV-007).
- [ ] **REV-T05 — Missing template fails and does not prune** (REV-005).
- [ ] **REV-T06 — Router console with chunked prompts** (REV-006).
- [ ] **REV-T07 — Lifecycle tier barrier** (REV-013).
- [ ] **REV-T08 — Builder reuse with changed environment/wrong template** (REV-014).
- [ ] **REV-T09 — Transport-level 404 for node/link** (REV-016).
- [ ] **REV-T10 — Numeric GNS3 versions `2.9` vs `2.10`** (REV-017).
- [ ] **REV-T11 — `lab_lifecycle --dataset`** (REV-018).
- [ ] **REV-T12 — Capture preflight with selected subset and synthetic statuses** (REV-019).
- [ ] **REV-T13 — Mirai choreography with a custom dataset** (REV-010).
- [ ] **REV-T14 — Custom inventory missing `address`/`environment`** (REV-012).
- [ ] **REV-T15 — Modbus timing flags and `freeze-heartbeat`** (REV-020).
- [ ] **REV-T16 — Merlin non-default scheme and malformed upload** (REV-021).
- [ ] **REV-T17 — Broker restart with TLS enabled** (REV-022).
- [ ] **REV-T18 — Partial TLS client cert/key** (REV-023).
- [ ] **REV-T19 — `build_topology` exit codes for each failure class** (REV-015).
- [ ] **REV-T20 — Unknown/absent switch `port_role`** (REV-036).

---

## Suggested fix order

1. **REV-002, REV-003, REV-004**: get CI green.
2. **REV-005, REV-006**: make the GNS3 build/config path safe before a live run.
3. **REV-007, REV-008, REV-009**: topology library correctness.
4. **REV-010, REV-011, REV-012**: attack-target contract.
5. **REV-013–REV-023**: remaining GNS3/device robustness.
6. **REV-026–REV-033**: documentation reconciliation in one pass.
7. **REV-024, REV-025, REV-034, REV-035, REV-036**: cleanup and smaller hardening.

## How to append a new issue

Add this at the end of the running list, replacing the placeholders:

```markdown
- [ ] **REV-XXX — Short imperative title.**
  - Evidence: file:line or command output.
  - Impact: what breaks or what is at risk.
  - Fix: the smallest correct change.
  - Verify: the exact test or command that proves it.
```

Keep the numbering monotonic. If an item is a duplicate, add a "Duplicate of
REV-xxx" note instead of a second entry.
