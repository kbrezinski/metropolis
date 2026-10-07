# OT Detection Systems

[![CI](https://github.com/kbrezinski/ot-detection-systems/actions/workflows/ci.yml/badge.svg)](https://github.com/kbrezinski/ot-detection-systems/actions/workflows/ci.yml)

Research on OT/ICS network traffic and provenance-based detection. A testbed is
a self-contained simulated network you can run, attack, and capture.

[Metropolis](testbeds/metropolis/) is the first testbed: a GNS3 water-treatment
plant, inspired by the reusable-device, router/switch, scenario, and
data-generation ideas of the [Gotham IoT Testbed](https://github.com/xsaga/gotham-iot-testbed)
and laid out along Purdue zones.

Each testbed owns its topology, IP assignments, node names, device deployments,
and attack targets. Shared schemas define reusable document formats; they do not
force testbeds to use the same CIDRs. Gotham scripts may be reused where their
assumptions fit.

The reference implementation is the [Gotham repository](https://github.com/xsaga/gotham-iot-testbed),
described in [“Gotham Testbed: A Reproducible IoT Testbed for Security Experiments and Dataset Generation”](https://doi.org/10.1109/TDSC.2023.3247166).
If you build on Gotham's work, follow its license and cite the paper as
appropriate.

## Where things stand

This is a work in progress, and it is worth knowing which parts are real before
you spend time on them.

| Area | State |
|---|---|
| Device simulators | Written and unit-tested; images build |
| Attack toolkit | Written and tested; runs against the inventory |
| GNS3 automation | Templates, topology build, lifecycle, and capture; unit-tested with a fake client, not yet run against a live server |
| Address plan, inventory, link plan, infrastructure, router and switch specs | Complete, and cross-checked by a validator |
| Dataset labelling and model training | **Not started** — future work |

Nothing in this repository has been verified on a live GNS3 deployment. The
network plan is complete, but the runbook below is a deployment plan rather than
a tested sequence.

## What you can do today

- **Build the device images** and create the nodes by hand in GNS3 from a
  complete inventory of addresses and roles.
- **Generate attack traffic** against those nodes with the experiment toolkit,
  which resolves every target from the inventory.
- **Validate the design** — the address plan, inventory, router interfaces,
  static routes, and switch VLANs are checked against each other by one command.
- **Capture your own traffic**, and record it under the versioned dataset folder.

## What is in the repository

- **Testbed definitions** — `testbeds/` holds one folder per environment:
  Metropolis router scripts, switch and VLAN specifications, device models, and
  its versioned dataset.
- **Reusable schemas** — `schemas/` defines shared formats: the network address
  plan, the device inventory, the infrastructure inventory, and the link plan.
  Each testbed stores its own concrete allocations, nodes, and cabling, and the
  topology validator checks every document against its schema.
- **Emulated devices** — Dockerfiles and Python apps under
  `testbeds/metropolis/devices/`: Modbus TCP PLC/RTU models, MQTT telemetry, a
  broker, SCADA, HMI, historian, and an engineering workstation. There is also
  an attack-lab set of synthetic C2 and botnet nodes.
- **Normal-operation traffic** — a routine Modbus client, local DNS and NTP, and
  a legacy Telnet host, so captures contain more than attacks.
- **Experiment toolkit** — `scripts/attacks/` generates attack traffic. Start
  with [its README](scripts/attacks/README.md).
- **Research package** — `src/metropolis/` provides runtime and reproducibility
  scaffolding. It is not yet connected to capture, labelling, or training.
- **Gotham data pipeline** — `scripts/run_gotham_pipeline.py` cleans and labels
  the separately downloaded Gotham dataset. It does not build or run Metropolis.

## Network design

The plan uses separate sites and routed zones, following Purdue concepts as a
guide:

| Purdue-style area | Metropolis role | Planned networks |
|---|---|---|
| Levels 0-1 | Sensors, actuators, PLCs, and RTUs in process cells | `10.20.10.0/24` – `10.20.15.0/24` |
| Level 2 | HMIs and local control access | `10.20.50.0/24` – `10.20.53.0/24` |
| Level 3 | SCADA, engineering, historian, MQTT, and OT services | `10.20.20.0/24` – `10.20.23.0/24`, `10.20.30.0/24` |
| Level 3.5 | OT DMZ | `10.20.40.0/24` |
| Level 4 | Simulated enterprise network | `10.30.10.0/24` |
| Lab-only test zone | Attack-generation hosts | `10.99.10.0/24` |
| Inter-site transport | Simulated WAN underlay, with reserved IPsec overlays | `172.31.255.0/24`, overlays under `10.20.254.0/24` |

These ranges belong to Metropolis. Future testbeds can use the same schema with
different CIDRs, or reuse an allocation pattern deliberately when two
environments need to be comparable.

The topology covers a treatment plant, a raw-water lift station, and a
reservoir/booster station, with routers and switches organised by backbone and
location. See the [router scripts](testbeds/metropolis/router/), [switch
specifications](testbeds/metropolis/switch/README.md), and [v1 dataset
notes](testbeds/metropolis/datasets/water_treatment_v1/README.md).

**The routing is a draft.** Router scripts define interfaces and static routes,
but there are no firewall rules, the simulated WAN has no IPsec, and the
attack-test network is not yet restricted from the OT networks. The scripts
carry `TODO` reminders for that work. Do not treat the current draft as a
secured network.

## Device models

The emulated set focuses on Modbus TCP and MQTT:

| Role | What it does |
|---|---|
| PLC / RTU | Modbus TCP server with example process registers, a heartbeat, and writable pump/valve coils |
| Field sensor | Publishes periodic JSON telemetry over MQTT, and answers CoAP discovery and status |
| MQTT broker | Mosquitto on TCP 1883, requiring credentials |
| SCADA | Polls a Modbus controller and publishes a status snapshot over MQTT |
| HMI | Authenticated HTTP panel on TCP 8080; shows telemetry and publishes setpoints |
| Historian | Subscribes to MQTT topics and logs what it receives |
| Engineering workstation | OpenSSH shell and SFTP on TCP 22 |
| Routine control client | Resolves the PLC via DNS, samples NTP, and makes bounded periodic Modbus reads and writes |
| Legacy gateway | Telnet login and shell, for experiments needing legacy host access |
| DNS and NTP | Local name resolution and time replies, so the network has background traffic |

These are lightweight protocol simulators, not vendor firmware. The sample
process values are not connected by a hydraulic model, and the HMI setpoint is
not wired to PLC control logic — attacks have no physical consequence. Per-role
MQTT ACLs and TLS, a realistic process model, and complete cell-by-cell register
maps remain future work.

See [testbeds/metropolis/devices/README.md](testbeds/metropolis/devices/README.md)
for build commands, environment settings, topics, and register definitions.

## Running Metropolis on an Ubuntu GNS3 server

The intended host is Ubuntu with Docker and a GNS3 server reachable by the GNS3
client. **End-to-end startup is not automated**, and no step below has been
verified on a live deployment.

### 1. Prepare the host

Install Docker and GNS3 from their official instructions, then clone the
repository onto the machine that will build or run the GNS3 Docker nodes:

```bash
git clone https://github.com/kbrezinski/ot-detection-systems.git
cd ot-detection-systems
```

For a remote GNS3 server, build or transfer images to the Docker host that
server uses. Images that exist only on your desktop are not visible to it.

### 2. Build the device images

From the repository root:

```bash
docker build -f testbeds/metropolis/devices/controllers/plc/Dockerfile -t metropolis/controller:dev .
docker build -f testbeds/metropolis/devices/services/mqtt_broker/Dockerfile -t metropolis/mqtt-broker:dev .
docker build -f testbeds/metropolis/devices/field/sensors/Dockerfile -t metropolis/mqtt-sensor:dev .
docker build -f testbeds/metropolis/devices/operations/scada/Dockerfile -t metropolis/scada:dev .
docker build -f testbeds/metropolis/devices/operations/hmi/Dockerfile -t metropolis/hmi:dev .
docker build -f testbeds/metropolis/devices/operations/historian/Dockerfile -t metropolis/historian:dev .
docker build -f testbeds/metropolis/devices/operations/engineering_workstation/Dockerfile -t metropolis/engineering:dev .
```

The [device README](testbeds/metropolis/devices/README.md) lists the rest,
including the attack-lab images. These commands create local images only; they
do not create GNS3 templates or place nodes in a project.

### 3. Create the templates and topology

In GNS3, create Docker templates referencing the built images, import the VyOS
appliance, then add routers, switches, and device nodes to a project. Configure
switch VLAN membership and trunk ports from `testbeds/metropolis/switch/`, and
apply the router scripts to the matching VyOS nodes after checking interface
numbering.

Use `device_instances/initial_devices.yaml` as the node inventory. GNS3 does not
read this file, so copy each node's `NODE_HOSTNAME`, `NODE_IP`, and
`NODE_GATEWAY` into its Docker environment settings. The shared startup script
applies those to `eth0` (or `NODE_INTERFACE`) before the application starts;
leave `NODE_IP` empty to skip address setup. Templates, topology, router
startup, and switch setup are all manual. A topology builder that reads the
inventory is future work.

Connect each Docker node's interface before starting it, or the entrypoint will
not find an interface to configure.

### 4. Start and check the lab

Start the broker and controller before the MQTT clients and SCADA node. Check
each node's console for startup or connection messages, confirm routing between
the subnets you care about, then capture on the GNS3 links you selected. Record
the topology, addresses, image versions, scenario, capture points, and
timestamps under the matching dataset folder.

### 5. Generate attack traffic

With the lab running, use the experiment toolkit. Every tool takes a dry run
first:

```bash
python scripts/attacks/run.py network_recon_scan --dry-run      # nothing sent
export METROPOLIS_LAB_ACK=yes
python scripts/attacks/run.py network_recon_scan                # scans the lab
```

See [scripts/attacks/README.md](scripts/attacks/README.md) for the full list,
and [the service guide](scripts/attacks/SERVICE_TARGETS.md) for what each
service normally looks like.

## Known limitations

Worth repeating, because these bound what you can conclude:

- **No firewall or IPsec.** The attack-test network is not restricted from the
  OT networks, and routing provides reachability rather than containment.
- **No encrypted traffic.** MQTT is plaintext and CoAP has no DTLS, so nothing
  here exercises detection under encryption.
- **No physical process model.** Modbus writes change registers without
  affecting any simulated process, so "attack detected" and "process affected"
  are independent.
- **No orchestration.** Topology creation, capture, and scenario playback are
  manual; the repository provides the devices and the traffic generator.
- **Not verified in deployment.** Everything is unit- and loopback-tested, but
  no end-to-end run on GNS3 has been performed.

## Dataset layout

Each experiment should have a versioned dataset folder, for example
`testbeds/metropolis/datasets/water_treatment_v1/`:

```text
testbeds/metropolis/datasets/water_treatment_v1/
├── topology/          # Sites, address plan, routers, switches, and links
├── device_instances/  # Named nodes, addresses, roles, and image settings
├── protocol_profiles/ # Modbus maps, MQTT topics, and security settings
├── scenarios/         # Normal operation and individual experiments
├── metadata/          # Capture points, labels, timestamps, and run details
└── captures/          # PCAPs; generated captures are Git-ignored
```

Keep reusable device behaviour under `testbeds/metropolis/devices/`, and per-run
assignments under the versioned dataset. Create a new dataset version rather
than silently changing the configuration behind an existing capture set.

## Python package

The `metropolis` package targets Python 3.12+ and uses [uv](https://docs.astral.sh/uv/):

```bash
uv sync --group dev
uv run python -c "import metropolis; print(metropolis.__file__)"
uv run pytest
```

The import package is `metropolis`; the distribution is
`metropolis-ot-detection-systems`. It provides runtime configuration helpers and
`metropolis.testbeds.load_address_plan()` for loading a bundled address plan as
a Python mapping:

```python
from metropolis.testbeds import load_address_plan

plan = load_address_plan()
print(plan["testbed_id"], len(plan["networks"]))
```

The loader accepts `testbed_id` and `dataset_version` for future testbeds and
versions. Data ingestion, feature extraction, provenance modelling, and
evaluation remain future work. For a runnable example that imports the package
and loads the v1 plan into a pandas table, see
[`examples/test_metropolis_import.ipynb`](examples/test_metropolis_import.ipynb).
The address plan ships in built distributions, so the loader works without a
checkout.

To cross-check the address plan, device inventory, router interfaces and static
routes, and switch VLAN specs, run:

```bash
uv run python scripts/validate_metropolis_topology.py
```

Add `--report testbeds/metropolis/datasets/water_treatment_v1/topology/validation-report.md`
to save a readable cross-reference.

## Project layout

```text
schemas/                                  Reusable testbed configuration schemas
testbeds/metropolis/                      Metropolis testbed assets
  router/                                 VyOS router configuration drafts
  switch/                                 GNS3 switch and VLAN specifications
  devices/                                Dockerfiles and Python device simulators
  datasets/                               Versioned topology, instances, scenarios, and captures
scripts/attacks/                          Attack traffic generator
scripts/gns3/                             GNS3 controller automation
scripts/run_gotham_pipeline.py            Gotham CSV cleaning and labelling
src/metropolis/                           Python research package
tests/                                    Tests for the Python package and toolkit
```

## Development

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
```

GitHub Actions runs lint, formatting checks, tests, package builds, and an import
smoke test. These checks do not build or start the GNS3 testbed.
