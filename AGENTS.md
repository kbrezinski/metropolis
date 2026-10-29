# AGENTS.md

Repository guidance. Do not update CLAUDE.md.

## Scope and planning

- Metropolis is a GNS3 water-treatment OT testbed with versioned topology, simulated devices, attacks, and capture tools. The PyTorch package is early-stage.
- Only category 1 (testbed and attacks) is active. Data processing, models, experiments, and paper work require a user request.
- Read and update root `PLANS.md` for priorities, decisions, and all feature proposals. Reuse existing entries, keep plans brief, expand substantial changes into executable plans before coding, and remove completed tasks.
- Use [PAPER_PLAN.md](docs/paper/PAPER_PLAN.md) for paper objectives, writing, and exploratory ideas/questions; put paper artifacts in `docs/paper/`.

## Dev environment tips

- Use Python 3.12 and uv: `uv sync --group dev`. Package code is under `src/`; GNS3 and attack tools are under `scripts/`.
- Keep dev dependency pins aligned with `scripts/attacks/requirements.txt` for pymodbus, paho-mqtt, paramiko, and scapy.
- Scripts/devices are not regular packages. Tests use `importlib.util.spec_from_file_location` or add their directory to `sys.path`.

## Testing instructions

- For unit test authoring, review, or audits, use [metropolis-test-audit](.agents/skills/metropolis-test-audit/SKILL.md).

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run python scripts/validate_metropolis_topology.py
uv run python scripts/generate_switch_reference.py --check
uv run python -m metropolis.topology check
python scripts/gns3/run.py register_templates --check
python scripts/gns3/run.py build_topology --plan
python scripts/gns3/run.py render_firewall --check
python scripts/attacks/run.py network_recon_scan --dry-run
```

- Run focused pytest tests for changed simulator/tooling behavior, then applicable validation commands. Another instance of an existing model needs no duplicate unit tests.
- Validate selected-dataset inventory, addresses, links, router configuration, and switch roles with the standalone topology validator; avoid exact node/IP/count assertions in pytest.
- Keep pytest caches and temporary files, including custom `--basetemp`, under ignored `.venv/`; never create root `.pytest_*` directories.
- The topology validator, firewall renderer, and attack dry run need no server. CI also checks builds, spelling, and links.
- Inspect skipped build-plan links; exit status alone is insufficient. Offline checks validate declarations, not actual cabling, built images, service startup, packets, or logs.
- The user verifies live GNS3 cabling, VLAN isolation, OSPF, firewall enforcement, and traffic. Do not claim live topology/routing verification from offline checks.

## Topology and device reference

- Authoritative dataset: `testbeds/metropolis/datasets/water_treatment_v1/`. Its four documents are `topology/address-plan.yaml`, `topology/links.yaml`, `device_instances/initial_devices.yaml`, and `device_instances/infrastructure.yaml`.
- Resolve addresses from the selected dataset; never hardcode them in reusable tools. Schemas live in `schemas/`; keep them aligned with `src/metropolis/topology/` load/save behavior. The bundled address-plan loader uses package resources.
- After topology edits, run the validator and `scripts/generate_switch_reference.py --write`. Never hand-edit generated `testbeds/metropolis/switch/README.md`.
- Reusable Docker models live in `testbeds/metropolis/devices/`. Keep inventory, links, image/template registration, startup order, and focused tests aligned.

| Cell/site | Controller and field devices |
|---|---|
| Intake | PLC; three cyclic EtherNet/IP actuators |
| Dosing | PLC; Modbus analyzer; two cyclic drives |
| Filtration | PLC; Modbus differential-pressure instrument; cyclic backwash pump and valve |
| Disinfection | PLC; Modbus residual analyzer; two cyclic pump drives |
| Reservoir and raw-water (each) | RTU; Modbus level instrument; cyclic pump drive |

- Central SCADA polls four PLCs and two RTUs over Modbus and polls remote RTU alarm events over DNP3; HMI setpoint readback covers plant cells. The control client reads the intake sensor's CoAP status. These are lab protocol models, not vendor profiles or physical process simulation.
- Router scripts in `testbeds/metropolis/router/` use OSPF area 0. Firewall policy permits selected LANs/CIDRs by protocol/port and drops other routed traffic; same-VLAN traffic bypasses it.
- The WAN is an untrusted public-network underlay; site-to-site IPsec is planned, not configured. VyOS is imported separately; `scripts/gns3/switch_config.py` configures the repo-owned Open vSwitch image.

## Node change workflow

1. Define role and normal traffic. Choose unique names/addresses avoiding collisions with hosts, router interfaces, gateways, and reservations; align inventory environment and ports with the model.
2. For new images, provide Docker builds and template entries, adapters, and startup dependencies. Build on the hosting GNS3 compute; the offline registry check only compares image names.
3. Declare links/switch roles or `link_policy: intentionally_uncabled` for deliberately unlinked support nodes; regenerate the switch reference. Existing subnets need no new route; new subnets need VLAN/trunk, router interface/gateway, and OSPF changes.
4. Check service targets, protocols, and startup/readiness. Run `render_firewall --check` for routed flows; change permissions only as needed.
5. Check capture points and log attribution, run applicable tests/validation, and update owning docs and `PLANS.md` when scope/status changes.

The builder reuses nodes by name without reconciling changed images or environments; it reports stale MQTT/DNP3/CoAP environment settings.

## Runs, attacks, and documentation

- `scripts/gns3/run.py` dispatches builder, router/switch, lifecycle, capture, and experiment commands using the selected dataset; see `scripts/gns3/README.md` for usage and limits.
- Share run IDs across manifests, timelines, captures, and logs. Keep timeline actions separate from service logs; record capture/collector failures and restore baseline firewall policy when a run with temporary attack allowances ends.
- `scripts/attacks/run.py` dispatches lab-only attacks. Resolve targets from the selected dataset unless explicitly overridden. Live runs require `METROPOLIS_LAB_ACK=yes`; use `--dry-run` offline. Keep bounded Python tools distinct from optional external scripts.
- Keep root README as the entry point. Put GNS3 setup/image builds in `scripts/gns3/README.md`, attacks in `scripts/attacks/README.md`, and device settings in the device README. Update and link the owning guide instead of duplicating setup steps.
