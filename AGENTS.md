# AGENTS.md

Guidance for agents working in this repository. Do not update CLAUDE.md.

## Current scope

Metropolis is a GNS3 water-treatment OT testbed with versioned topology data, simulated devices, attack traffic, and experiment capture tools. The PyTorch detection package is early-stage; data processing, model development, experiments, and paper work remain future work unless the user asks for them.

## ExecPlans

Read PLANS.md when discussing or changing priorities. Category 1 (testbed and attack scripts) is active. Record future ideas and decisions in PLANS.md without starting categories 2–4. Keep the plan brief. For substantial implementation, expand the relevant item into an executable plan before coding. Remove completed tasks rather than checking them off.

For paper writing and brainstorming, read and update [docs/paper/PAPER_PLAN.md](docs/paper/PAPER_PLAN.md). It holds paper objectives, writing tasks, and exploratory feature ideas or research questions. Record all new feature proposals and implementation tasks in root PLANS.md, reusing existing entries rather than duplicating them. Paper drafts and supporting material belong in docs/paper/.

Python 3.12 is managed with uv. The package uses a src/ layout; GNS3 and attack tooling live under scripts/.

## Useful commands

    uv sync --group dev
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

The topology validator, firewall renderer, and dry run above do not need a live GNS3 server. CI also checks the build, spelling, and links. Use pytest for reusable simulator and tooling behavior; use the standalone topology validator for the selected dataset's inventory, addresses, links, router configuration, and switch roles. Adding another instance of an existing device model does not require a copy of its unit tests. Run focused tests for changed behavior, then the relevant validation commands above.

Pytest temporary files and caches belong under the ignored `.venv/` directory. If you override `--basetemp` for a separate test run, keep that path under `.venv/` as well; do not create `.pytest_*` directories at the repository root.

## Topology and devices

- The authoritative dataset is testbeds/metropolis/datasets/water_treatment_v1/. Its topology/address-plan.yaml, topology/links.yaml, device_instances/initial_devices.yaml, and device_instances/infrastructure.yaml define networks, links, hosts, routers, and switches. Schemas live in schemas/. Do not hardcode node addresses in reusable tools; resolve them from the selected dataset.
- After changing topology data, run scripts/validate_metropolis_topology.py and regenerate the switch reference with scripts/generate_switch_reference.py --write. The file testbeds/metropolis/switch/README.md is generated; do not edit it by hand.
- The topology API in src/metropolis/topology/ models the four authoritative documents. Keep its load/save behavior and the schemas in step. The bundled address-plan loader in src/metropolis/testbeds/ uses package resources.
- Reusable Docker device models live in testbeds/metropolis/devices/. Keep inventory, links, image/template registration, startup order, and focused tests aligned when changing a device. Intake has a PLC and three cyclic EtherNet/IP actuators. Dosing has a PLC, networked Modbus analyzer, and two cyclic drives. Filtration has a PLC, networked Modbus differential-pressure instrument, and cyclic backwash pump and valve. Disinfection has a PLC, networked Modbus residual analyzer, and two cyclic pump drives. The reservoir and raw-water sites each have an RTU, networked Modbus level instrument, and cyclic pump drive. Central SCADA polls the four PLCs and two RTUs; HMI setpoint readback applies to the plant cells. These are lab protocol models, not vendor profiles or a physical process simulation.
- Router scripts in testbeds/metropolis/router/ use OSPF area 0. The dataset firewall policy grants selected LANs or CIDRs access by protocol and port, with default drop for other routed traffic. Same-VLAN traffic bypasses router firewalls. The simulated WAN models an untrusted public-network underlay; site-to-site IPsec is planned and not configured. The switch configuration is applied by scripts/gns3/switch_config.py to the repo-owned Open vSwitch image. VyOS is a separately imported GNS3 appliance.

## Change workflow

For a new or changed node, check these in order:

1. Define its role and expected normal traffic. Give it a unique name and address that do not collide with hosts, router interfaces, gateways, or reserved addresses. Keep its inventory environment and service ports consistent with the device model.
2. Provide the Docker build and template registry entry for a new image, and account for its adapters and startup dependencies. Images must be built on the GNS3 compute that will host them; the offline registry check only compares image names.
3. Declare the intended link and switch port role, or set `link_policy: intentionally_uncabled` for a support node deliberately left without a link. Regenerate the switch reference. A new node on an existing subnet needs no new route; a new subnet also needs VLAN/trunk, router interface/gateway, and OSPF changes.
4. Check service targets, protocol behavior, and startup/readiness. Compile the network-level firewall policy with render_firewall --check for routed flows; same-VLAN traffic bypasses it. Add or change permissions only when needed.
5. Check whether capture points and log-source attribution can observe the new traffic. Add focused tests when simulator or tooling behavior changes; validate dataset wiring with the standalone topology command instead of exact node/IP/count assertions in pytest. Then update setup docs and PLANS.md when scope or status changes.

Run the topology validator, generated switch-reference check, template-registry check, build_topology --plan, and firewall renderer as applicable. Inspect the build plan's skipped links rather than relying on its exit code. The validator checks declared YAML, router scripts, and switch plans; it does not prove every intended device is cabled, an image is built, a service starts, or packets and logs appear. The builder reuses existing GNS3 nodes by name without reconciling changed image or environment settings. The user verifies actual GNS3 cabling, VLAN isolation, OSPF, firewall enforcement, and traffic in their GNS3 environment.

Keep the root README as the entry point. GNS3 setup and image builds belong in `scripts/gns3/README.md`, attack usage in `scripts/attacks/README.md`, and device settings in the device README. Update the owning guide and link to it instead of repeating setup sequences.

## Runs and attacks

- scripts/gns3/run.py is the GNS3 command entry point. Its builder, router/switch configuration, lifecycle, capture, and experiment commands use the selected dataset. See scripts/gns3/README.md for usage and limits. Do not claim live topology, route, VLAN, or firewall verification from offline checks; the user validates those in GNS3.
- Experiment runs share a run ID across manifest, timeline, captures, and logs. Keep timeline actions separate from device/service logs; record capture and collector failures. The firewall attack allowance is temporary and must restore the baseline when the run ends.
- scripts/attacks/run.py dispatches lab-only attacks. Targets resolve from the selected dataset unless explicitly supplied. Live attack commands require METROPOLIS_LAB_ACK=yes; use --dry-run for offline checks. Keep the bounded Python entry points and optional external scripts distinct.
- Scripts and device models are not regular importable packages. Their tests use importlib.util.spec_from_file_location or add the script directory to sys.path.
- Keep the dev dependency pins and scripts/attacks/requirements.txt aligned for pymodbus, paho-mqtt, paramiko, and scapy.
