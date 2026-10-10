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

A work in progress. The dates are when each piece landed; the entries marked
⬜ are not built yet.

| | Item | Date |
|---|---|---|
| ✅ | Reproducibility scaffolding: seed and device configuration, directory helpers, address-plan loader | 2026-04-22 |
| ✅ | Gotham data pipeline: feature cleaning, labelling, per-device preparation | 2026-09-19 |
| ✅ | Metropolis testbed design: Purdue-informed zones, address plan, device inventory | 2026-09-27 |
| ✅ | Router and switch specifications: six VyOS scripts, nine switch VLAN plans | 2026-09-27 |
| ✅ | Device models: PLC/RTU, sensor, broker, SCADA, HMI, historian, engineering workstation, control client, legacy gateway, DNS, NTP | 2026-10-01 |
| ✅ | Topology validator: cross-checks the address plan, inventory, router interfaces, static routes, and switch VLANs | 2026-10-06 |
| ✅ | MQTT over TLS: lab certificate authority, broker listener, verification by real handshake | 2026-10-06 |
| ✅ | Link plan: 40 links as data, with the schema and validator to check them | 2026-10-09 |
| ✅ | Infrastructure inventory: six routers, eight switches, and ten access segments | 2026-10-09 |
| ✅ | Four JSON schemas, enforced by the validator rather than decorative | 2026-10-09 |
| ✅ | Attack toolkit: CoAP, MQTT, Modbus, scanning, discovery, SSH, Telnet, bounded load | 2026-10-09 |
| ✅ | Synthetic Mirai chain and Merlin C2, with whole-lifecycle drivers | 2026-10-09 |
| ✅ | GNS3 automation: API client, template registration, topology builder, lifecycle, capture control | 2026-10-09 |
| ✅ | Router configuration: each VyOS script loaded onto its node and verified from the running config | 2026-10-09 |
| ✅ | Light process model: coupled PLC registers, so telemetry moves and the sensor agrees with the controller | 2026-10-09 |
| ✅ | Shared device TLS: sensor, SCADA, HMI, and historian can all encrypt, plus the toolkit's `--tls` | 2026-10-09 |
| ✅ | Documentation pass: READMEs rewritten around tasks, 250 tests, lint and spelling in CI | 2026-10-09 |
| ⬜ | Deploy and capture on a live GNS3 server | Future work |
| ⬜ | Captured packet data — `captures/` is empty | Future work |
| ⬜ | Firewall policy and IPsec, so routing restricts rather than merely reaches | Future work |
| ⬜ | CoAP over DTLS — CoAP is plaintext | Future work |
| ⬜ | More device families: Gotham has twelve, this has three | Future work |
| ⬜ | Protocol profiles — documented per-protocol settings | Future work |
| ⬜ | Scenarios — scripted experiment playback | Future work |
| ⬜ | Dataset labelling for Metropolis captures | Future work |
| ⬜ | Provenance modelling, training, and evaluation | Future work |

## What you can do today

- **Build the device images** and create the topology from the inventory with one
  command, rather than adding nodes by hand.
- **Generate attack traffic** against those nodes with the experiment toolkit,
  which resolves every target from the inventory.
- **Validate the design** — the address plan, inventory, link plan, router
  interfaces, static routes, and switch VLANs are checked against each other by
  one command.
- **Capture traffic** on chosen links, and record it under the versioned dataset
  folder.

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
client. The steps below build the images, register the templates, create the
topology, start the lab, apply the router configurations, and capture traffic.

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

The GNS3 automation does this from the declared topology. Import the VyOS and
Ethernet switch appliances once in the GUI — they are appliances rather than
images, so they cannot be registered like the device images — then:

```bash
python scripts/gns3/run.py register_templates --check   # registry vs inventory
python scripts/gns3/run.py register_templates           # one template per image
python scripts/gns3/run.py build_topology --plan        # what would be created
python scripts/gns3/run.py build_topology               # create project, nodes, links
```

`build_topology` reads `initial_devices.yaml`, `infrastructure.yaml`, and
`links.yaml`, so node names, addresses, and cabling come from those files rather
than being entered by hand. It also derives the GNS3 port numbers the switch
specifications leave as a `TODO`; `--write-ports` writes that mapping out.

### 4. Start the lab and load the router configurations

```bash
python scripts/gns3/run.py lab_lifecycle --start
python scripts/gns3/run.py build_topology --configure-routers
```

The lifecycle command starts switches, then routers, then publishers, then
polling clients, so nothing exhausts its retries against a node that is still
booting. `--configure-routers` logs into each VyOS console, applies its script,
and reports any address the router does not come back with.

Use `device_instances/initial_devices.yaml` as the node inventory. GNS3 does not
read this file, so copy each node's `NODE_HOSTNAME`, `NODE_IP`, and
`NODE_GATEWAY` into its Docker environment settings. The shared startup script
applies those to `eth0` (or `NODE_INTERFACE`) before the application starts;
leave `NODE_IP` empty to skip address setup. Templates, topology, router
startup, and switch setup are all manual. A topology builder that reads the
inventory is future work.

Connect each Docker node's interface before starting it, or the entrypoint will
not find an interface to configure.

### 5. Check the lab, then capture

Check each node's console for startup or connection messages and confirm routing
between the subnets you care about. Then capture on the links you selected:

```bash
python scripts/gns3/run.py capture_traffic --list
python scripts/gns3/run.py capture_traffic --links link-core-plant --duration 60
```

GNS3 writes each capture next to the project and the command reports where the
files landed. Record the topology, addresses, image versions, scenario, capture
points, and timestamps under the matching dataset folder.

### 6. Generate attack traffic

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
- **Encryption stops at CoAP.** MQTT over TLS works, with a lab CA and a
  verified handshake, and the sensor, SCADA, the HMI, and the historian can all
  use it. CoAP has no DTLS, so there is no encrypted CoAP traffic to capture.
- **A light process model, not a hydraulic one.** The PLC's registers are
  coupled to the pump coil and to each other, so telemetry moves and the sensor
  agrees with the registers. It is not a validated model of a water-treatment
  process, so a register write is not a model of operational damage.
- **Scenario playback is not built.** Templates, topology, router configuration,
  lifecycle, and capture are automated; running a *scripted* experiment that
  starts services, replays traffic, and stops captures on cue is not.

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
