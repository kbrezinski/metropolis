# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project

`metropolis-ot-detection-systems` (import name `metropolis`) is an early-stage PyTorch project for detecting provenance/attacks in OT (operational technology) network traffic; `pyproject.toml` still has a placeholder `description`. Two things coexist in this repo:

- The Python package provides runtime/reproducibility helpers and `metropolis.testbeds.load_address_plan()` for loading bundled topology data: `src/metropolis/`. `src/metropolis/core/` is an empty package and the root `main.py` is a hello-world stub.
- A synthetic OT testbed ("Metropolis", a GNS3 water-treatment network) plus a versioned dataset layout under `testbeds/<testbed_id>/datasets/` that will supply the labelled traffic `metropolis` eventually trains on — see **Metropolis testbed & dataset layout** below. This is config/spec authoring, not Python.

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
```

CI (`.github/workflows/`) runs ruff check, `ruff format --check`, pytest, `uv build`, and an import smoke test on ubuntu/windows/macos; codespell and a lychee link check (README and `**/*.html`) run as separate workflows. Ruff has no config in `pyproject.toml`, so defaults apply.

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

- `testbeds/metropolis/router/backbone/` and `testbeds/metropolis/router/locations/` — VyOS 1.3 `vbash` scripts (`set interfaces ...`, `commit`, `save`) for each GNS3 router, one per network zone (plant, OT core, OT/DMZ boundary, WAN hub, reservoir, raw-water). Each script's header comment documents its GNS3 interface map and which VLANs/subnets it must carry; routing between zones is via static routes only, and several scripts carry `TODO(Metropolis)` notes that inter-zone firewall policy is not yet applied — static routes provide reachability, not restriction.
- `testbeds/metropolis/switch/` — Markdown specs (not executable), read together with `testbeds/metropolis/switch/README.md`'s port-mapping table, describing the VLAN plan for each GNS3 switch appliance. Port labels like `router-uplink` are roles, not literal GNS3 port numbers, filled in after cabling.
- `testbeds/metropolis/devices/` — reusable Docker-based device models. The initial runnable set includes a Modbus TCP PLC/RTU simulator, MQTT sensor publisher, Mosquitto broker, SCADA Modbus poller/MQTT publisher, HMI command publisher, MQTT historian subscriber, and engineering toolbox image. Model code and image build instructions are documented in `testbeds/metropolis/devices/README.md`; other device folders remain planned scaffolding.
- `testbeds/metropolis/datasets/water_treatment_v1/` is Metropolis versioned data; future testbeds keep datasets under their own `testbeds/<testbed_id>/datasets/` directory. Its `topology/address-plan.yaml` records concrete CIDRs using `schemas/address-plan.schema.json`; the shared schema does not prescribe ranges. Dataset folders also contain `device_instances/` (addresses/roles assigned from `testbeds/metropolis/devices/`), `protocol_profiles/`, `scenarios/`, `metadata/`, and `captures/`. Captures are git-ignored (`testbeds/*/datasets/*/captures/**`); use a new versioned directory for each distinct dataset.

The address-plan YAML is bundled with the Python distribution and loaded through `metropolis.testbeds.load_address_plan()`. GNS3 topology startup, traffic capture, and scenario orchestration are not yet wired into the package; many testbed directories remain specs/scaffolding with `.gitkeep` placeholders.
