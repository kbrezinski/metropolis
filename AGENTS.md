# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project

`metropolis-ot-detection-systems` (import name `metropolis`) is an early-stage PyTorch project for detecting provenance/attacks in OT (operational technology) network traffic. Two things coexist in this repo:

- The Python package provides runtime/reproducibility helpers and `metropolis.testbeds.load_address_plan()` for loading bundled topology data: `src/metropolis/`. `src/metropolis/core/` is an empty package and the root `main.py` is a hello-world stub.
- A synthetic OT testbed ("Metropolis", a GNS3 water-treatment network) plus a versioned dataset layout under `testbeds/<testbed_id>/datasets/` that will supply the labelled traffic `metropolis` eventually trains on — see **Metropolis testbed & dataset layout** below. The testbed data is declarative YAML/config. Its tooling lives under `scripts/` (GNS3 automation, validation, attack traffic), outside the `metropolis` package.

Python 3.12 (`.python-version`), managed with **uv**; build backend is hatchling with a `src/` layout (`packages = ["src/metropolis"]`).

## Commands

```bash
uv sync --group dev                      # install runtime + dev deps (CI uses --locked)
uv run pytest                            # all tests (testpaths=tests, -q, importlib import mode)
uv run pytest tests/test_config.py::test_seed_everything_repeats_python_numpy_and_torch_streams   # single test
uv run ruff check . --fix                # lint
uv run ruff format .                     # format
uv run codespell . --skip="./.git,./.venv,./dist,./build,./uv.lock"   # spelling (CI runs this too)
uv build                                 # build wheel/sdist (CI does this)
pre-commit run --all-files               # end-of-file/whitespace, ruff, codespell
uv run python scripts/run_gotham_pipeline.py   # clean + label + prepare the CSVs in data/ (see below)
uv run python scripts/validate_metropolis_topology.py       # cross-check address/inventory/link/router/switch plans
uv run python scripts/generate_switch_reference.py --check  # is switch/README.md current? (--write regenerates)
uv run python -m metropolis.topology show                   # topology library CLI: show/check/render/export
uv run python examples/build_a_topology.py --draw           # build a small topology in code
python scripts/gns3/run.py build_topology --plan            # GNS3 automation; --plan/--list/--order need no server
python scripts/attacks/run.py network_recon_scan --dry-run  # attack toolkit; real runs need METROPOLIS_LAB_ACK=yes
```

CI (`.github/workflows/`) runs ruff check, `ruff format --check`, pytest (after `uv pip install -r scripts/attacks/requirements.txt`), `uv build`, and an import smoke test on ubuntu/windows/macos; codespell and a lychee link check (README and `**/*.html`) run as separate workflows. Ruff has no config in `pyproject.toml`, so defaults apply. codespell ignores `ot` and `checkin` via `[tool.codespell]`.

`pymodbus`, `paho-mqtt`, `paramiko` and `scapy` are pinned identically in the `dev` group and `scripts/attacks/requirements.txt` (pymodbus 4.x breaks the Modbus controller); keep the two lists in step.

## Architecture

**Determinism is a first-class concern.** Reproducibility is enforced in two places that must stay consistent:

- `config.py` — `RuntimeConfig` (frozen, slotted dataclass) holds seed/device/determinism knobs. `apply_runtime_config()` seeds Python/NumPy/torch via `seed_everything()` and returns a *new* config with `device="auto"` resolved to `"cuda"`/`"cpu"`. `seed_everything()` calls `torch.use_deterministic_algorithms(...)`, so any torch op lacking a deterministic implementation raises.
- `tests/conftest.py` — an autouse fixture reseeds every RNG and turns on deterministic algorithms for each test. It also sets `PYTHONHASHSEED` and `CUBLAS_WORKSPACE_CONFIG` at the top of the file, *before* importing torch; these must be set before torch initializes CUDA backends, so keep them above the `import torch` line.

`seed_everything()` takes `cudnn_benchmark` and `cudnn_deterministic` as well; `cudnn_deterministic=None` falls back to the `deterministic` arg. `apply_runtime_config()` forwards all the `RuntimeConfig` knobs.

**Paths** — `paths.py` defines the data/models/artifacts directory constants (`RAW_DATA_DIR`, `CHECKPOINTS_DIR`, `LOGS_DIR`, …) and `ensure_directories()` to create them. `_BASE_DIR` is the repo root (`parents[2]` from `src/metropolis/paths.py`). That only holds for an editable/source checkout; from an installed wheel it would point into `site-packages`' parent. `.gitignore` ignores `data/`, `models/`, and `artifacts/`.

**Bundled testbed data** — `metropolis.testbeds.load_address_plan(testbed_id="metropolis", dataset_version="water_treatment_v1")` returns the parsed address-plan mapping. The YAML is sourced from `testbeds/` in the checkout and force-included into the wheel under `metropolis/data/testbeds/`; use `importlib.resources` so installed users do not need a repository checkout. PyYAML is a direct runtime dependency.

Tests that touch device selection monkeypatch `config_mod.torch.cuda.is_available` rather than requiring a GPU.

**Gotham labelling pipeline** — `scripts/run_gotham_pipeline.py` runs feature cleaning, labelling and per-device data preparation on the tshark feature CSVs in `data/benign` and `data/malicious/<event>` (inputs are git-ignored). It clones [gotham-network-packet-labeller](https://github.com/othmbela/gotham-network-packet-labeller) into the git-ignored `third_party/` at a pinned commit and reuses its `Labeller`/`FeatureCleaner` and metadata rather than running its scripts, which at that commit crash or silently mislabel (glob-string bug in `run_cleaning.py`, `network-scanning` looks for a nonexistent metadata file, prefix-glob device matching merges `-1` with `-10`..`-19`). Non-obvious behaviour: the benign CSVs in this copy of the dataset hold a *different* device's traffic than their names say, so benign files are assigned to a device by the IP addresses in their traffic (audit trail: `data/benign_file_mapping.json`); malicious files are trusted as named. Outputs go to `data/labelled/`, `data/processed/<device>.csv` and `data/cleaning_report.json`; reruns skip existing outputs unless `--force`. Files are read in chunks, and cleaning samples each file down to ~200k rows (`--clean-max-rows`), because the largest CSV is >1 GB.

**Metropolis testbed & dataset layout** — The `testbeds/metropolis/` directory describes a simulated OT/ICS network (a water-treatment plant) built in GNS3, independent of the Gotham pipeline above:

- `testbeds/metropolis/router/backbone/` and `testbeds/metropolis/router/locations/` — VyOS 1.3 `vbash` scripts (`set interfaces ...`, `commit`, `save`) for each GNS3 router, one per network zone (`router_ot_core.sh`, `router_ot_dmz.sh`, `router_ot_wan.sh`, `router_plant.sh`, `router_reservoir.sh`, `router_raw_water.sh`). Each script's header comment documents its GNS3 interface map and which VLANs/subnets it must carry; routing between zones is via static routes only, and several scripts carry `TODO(Metropolis)` notes that inter-zone firewall policy is not yet applied — static routes provide reachability, not restriction.
- `testbeds/metropolis/switch/README.md` — **generated, do not hand-edit.** `scripts/generate_switch_reference.py` writes it from `device_instances/infrastructure.yaml` and `topology/links.yaml`; `tests/test_switch_reference.py` fails if it is stale. A switch port's number is the position of its role (e.g. `router-uplink`) in that switch's `attachment_points` list in `infrastructure.yaml`, so reordering that list renumbers ports. `links.yaml` deliberately names roles, never port numbers.
- `testbeds/metropolis/devices/` — reusable Docker-based device models. The initial runnable set includes a Modbus TCP PLC/RTU simulator, MQTT sensor publisher, Mosquitto broker, SCADA Modbus poller/MQTT publisher, HMI command publisher, MQTT historian subscriber, and engineering toolbox image. `testbeds/metropolis/devices/runtime/network-entrypoint.sh` applies per-node `NODE_HOSTNAME`, `NODE_IP`, `NODE_GATEWAY`, and optional `NODE_INTERFACE` settings before the app starts; GNS3 must provide the variables and the node's NIC must be linked. Model code and image build instructions are documented in `testbeds/metropolis/devices/README.md`; other device folders remain planned scaffolding.
- `testbeds/metropolis/datasets/water_treatment_v1/` is Metropolis versioned data; future testbeds keep datasets under their own `testbeds/<testbed_id>/datasets/` directory. Its `topology/address-plan.yaml` records concrete CIDRs using `schemas/address-plan.schema.json`; the shared schema does not prescribe ranges. Other dataset folders contain `device_instances/` (addresses/roles assigned from `testbeds/metropolis/devices/`), `protocol_profiles/`, `scenarios/`, `metadata/`, and `captures/`. Captures are git-ignored (`testbeds/*/datasets/*/captures/**`); use a new versioned directory for each distinct dataset.

The address-plan YAML is bundled with the Python distribution and loaded through `metropolis.testbeds.load_address_plan()`. Some testbed directories remain scaffolding with `.gitkeep` placeholders.

**Single source of truth for topology** — The YAML under `datasets/water_treatment_v1/` (`topology/address-plan.yaml`, `topology/links.yaml`, `device_instances/initial_devices.yaml` for hosts, `device_instances/infrastructure.yaml` for routers/switches) is authoritative, validated against `schemas/*.schema.json`. Everything else is derived from or checked against it: `scripts/validate_metropolis_topology.py` cross-checks it, including parsing the `# ethN -> neighbour` interface-map comments in the router scripts. It needs `jsonschema` from the dev group and never contacts GNS3. When changing topology, edit the data, then re-run the validator and regenerate the switch reference.

**Topology library (`src/metropolis/topology/`)** — The same four documents as a Python API: build a topology with `Network`/`Router`/`Switch`/`Segment`/`Device`/`Link` (`Endpoint.parse` reads `"NODE:eth1"` as an interface and `"NODE:role"` as a switch port role), check it with `problems()`/`validate()`, write it with `save()`, and draw it with `to_dot()`/`to_mermaid()`. `Topology.from_directory()` and `from_bundled()` read datasets; the CLI is `python -m metropolis.topology show|check|render|export`. `tests/test_topology_library.py` pins the strongest property: loading the bundled dataset and writing it back reproduces all four documents exactly, so the library is a complete model of the data rather than an approximation. Round-tripping loses `#` comments (PyYAML does not preserve them) but never data. `scripts/generate_switch_reference.py` renders through this library. Keep the schemas and this module in step; the schema is still what `validate_metropolis_topology.py` enforces.

**GNS3 automation (`scripts/gns3/`)** — Builds the declared topology on a GNS3 v2 server: `topology.py` turns the dataset into a plan, `builder.py` creates nodes and links idempotently (existing ones are reused, `--prune-missing` deletes extras), `lifecycle.py` orders startup and shutdown, `router_config.py` pushes the VyOS scripts over the raw-Telnet console and checks the result, and `capture.py` captures on chosen links. Everything goes through `run.py <command>`. The modules use flat sibling imports (`from client import Gns3Client`), so they run as scripts rather than as a package, and tests put `scripts/gns3` on `sys.path`. Server settings come from the newest `~/.config/GNS3/<ver>/gns3_server.conf`, overridden by `GNS3_SERVER_HOST`/`_PORT`/`_USERNAME`/`_PASSWORD`. VyOS and Open vSwitch are GUI-imported appliances (named in `templates.APPLIANCES`), not registered Docker templates.

**Attack toolkit (`scripts/attacks/`)** — Lab-only traffic generators (recon, MQTT/SSH brute force, MQTT flood, CoAP, Modbus writes, Mirai/Merlin choreographies) dispatched through `run.py`. Tools resolve targets from `initial_devices.yaml` rather than hardcoding addresses. Every tool supports `--dry-run` and sends nothing unless `METROPOLIS_LAB_ACK=yes`. `METROPOLIS_PYTHON` selects the interpreter that has the toolkit dependencies. Shared helpers live in the `_`-prefixed modules. TLS settings shared by devices and attacks are in `testbeds/metropolis/devices/runtime/tls_config.py`, and lab certificates come from `scripts/gen_lab_certificates.py`.

**Tests for non-package code** — Scripts and device models are not importable as packages. Tests load them with `importlib.util.spec_from_file_location` or by inserting their directory into `sys.path`. Follow the same pattern for new tests.

`CLAUDE.md` is a near-copy of this file for Claude Code; keep the two in step when changing either.
