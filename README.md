# OT Detection Systems

[![CI](https://github.com/kbrezinski/ot-detection-systems/actions/workflows/ci.yml/badge.svg)](https://github.com/kbrezinski/ot-detection-systems/actions/workflows/ci.yml)

This repository supports research on OT/ICS network traffic and provenance-based detection, with testbeds organized as independent, named environments. [Metropolis](testbeds/metropolis/) is the first testbed: a GNS3 water-treatment network inspired by the reusable-device, router/switch, scenario, and data-generation ideas of the [Gotham IoT Testbed](https://github.com/xsaga/gotham-iot-testbed), with a Purdue-informed architecture.

Each testbed owns its topology, IP assignments, node names, device deployments, and attack targets. Shared schemas define reusable document formats; they do not force testbeds to use the same CIDRs. Gotham scripts and tooling may be reused where their assumptions fit. This repository is under active development; the sections below distinguish current artifacts from work still needed for an end-to-end run.

The reference implementation is the [Gotham repository](https://github.com/xsaga/gotham-iot-testbed), described in [“Gotham Testbed: A Reproducible IoT Testbed for Security Experiments and Dataset Generation”](https://doi.org/10.1109/TDSC.2023.3247166). If you build on Gotham's work, follow its license and cite the paper as appropriate.

## What is in the repository

- **Testbed definitions:** `testbeds/` contains one folder per network environment. Metropolis router scripts, switch/VLAN specifications, device models, and its versioned dataset are under `testbeds/metropolis/`.
- **Reusable schemas:** `schemas/` defines shared data formats, including a network address-plan schema. Each testbed stores its own concrete network plan and IP allocations.
- **Reusable emulated devices:** Dockerfiles and Python applications under `testbeds/metropolis/devices/`, including Modbus TCP PLC/RTU models, MQTT telemetry and broker components, SCADA/HMI simulators, a historian collector, and an engineering workstation image.
- **Dataset organization:** `testbeds/metropolis/datasets/water_treatment_v1/` holds the planned topology, device instances, protocol profiles, scenarios, metadata, and capture locations. `device_instances/initial_devices.yaml` is an initial inventory proposal.
- **Research package:** `src/metropolis/` currently provides Python runtime and reproducibility scaffolding. It is not yet connected to traffic capture, dataset labeling, or model training for Metropolis.
- **Existing Gotham data pipeline:** `scripts/run_gotham_pipeline.py` is for processing the separately downloaded Gotham dataset in `data/`; it does not build or run the Metropolis GNS3 network.

## Network design

The plan uses separate sites and routed zones, following Purdue concepts as a guide:

| Purdue-style area | Metropolis role | Planned networks |
|---|---|---|
| Levels 0-1 | Sensors, actuators, PLCs, and RTUs in process cells | `10.20.10.0/24` through `10.20.15.0/24` |
| Level 2 | HMIs and local control access | `10.20.50.0/24` through `10.20.53.0/24` |
| Level 3 | SCADA, engineering, historian, MQTT, and OT services | `10.20.20.0/24` through `10.20.23.0/24`, plus `10.20.30.0/24` |
| Level 3.5 | OT DMZ | `10.20.40.0/24` |
| Level 4 | Simulated enterprise network | `10.30.10.0/24` |
| Lab-only test zone | Attack-generation hosts for controlled experiments | `10.99.10.0/24` |
| Inter-site transport | Simulated WAN underlay; remote sites have reserved IPsec overlay ranges | `172.31.255.0/24`; overlays reserved under `10.20.254.0/24` |

These ranges belong to Metropolis. Future testbeds can use the shared address-plan schema with different CIDRs, or intentionally reuse an allocation pattern when the environments need to be comparable.

The Metropolis topology has a treatment plant, a raw-water lift station, and a reservoir/booster station. Routers and switches are organized by backbone and location, similar to Gotham's site-oriented network organization. See the [router scripts](testbeds/metropolis/router/), [switch specifications](testbeds/metropolis/switch/README.md), and [v1 dataset notes](testbeds/metropolis/datasets/water_treatment_v1/README.md) for details.

The current router scripts provide draft routing and VLAN interfaces. Firewall rules are not configured, the simulated WAN is not encrypted with IPsec, and the attack-test network is not yet restricted from OT networks. The scripts contain TODO reminders for that work. Do not treat the current routing draft as a secured network.

## Device models and protocols

The initial emulated device set focuses on Modbus TCP and MQTT:

| Role | Current model behavior |
|---|---|
| PLC / RTU | A small Modbus TCP server with example process registers, a heartbeat, and writable pump/valve coils |
| Field sensor | Publishes periodic JSON MQTT telemetry |
| MQTT broker | Mosquitto broker listening on TCP 1883 |
| SCADA | Polls a Modbus TCP controller and publishes a status snapshot over MQTT |
| HMI | Publishes a periodic example setpoint over MQTT |
| Historian | Subscribes to MQTT topics and logs received messages to its console |
| Engineering workstation | Container with Python Modbus/MQTT libraries for lab interaction |

These are lightweight protocol simulators, not vendor device firmware. The sample process values are not connected by a hydraulic model, the HMI setpoint is not yet wired to PLC control logic, and the MQTT broker currently permits anonymous connections. See [testbeds/metropolis/devices/README.md](testbeds/metropolis/devices/README.md) for image build commands, environment settings, topics, and register definitions. Authentication, MQTT ACLs/TLS, realistic process behavior, and complete cell-by-cell register maps are future work.

## Running Metropolis on an Ubuntu GNS3 server

The intended deployment host is Ubuntu with Docker and a GNS3 server available to the GNS3 client. The following describes the planned workflow; **end-to-end startup is not automated yet**.

### 1. Prepare the host and repository

Install and configure Docker and GNS3 using their official instructions, then clone this repository onto the machine that builds or runs the GNS3 Docker nodes:

```bash
git clone https://github.com/kbrezinski/ot-detection-systems.git
cd ot-detection-systems
```

Confirm Docker can build and run containers on the GNS3 compute host. For a remote GNS3 server, build or transfer the images to the Docker host used by that server; images available only on your desktop will not automatically exist on the server.

### 2. Build the device images

From the repository root, run the image build commands in [testbeds/metropolis/devices/README.md](testbeds/metropolis/devices/README.md). For example:

```bash
docker build -f testbeds/metropolis/devices/controllers/plc/Dockerfile -t metropolis/controller:dev .
docker build -f testbeds/metropolis/devices/services/mqtt_broker/Dockerfile -t metropolis/mqtt-broker:dev .
docker build -f testbeds/metropolis/devices/field/sensors/Dockerfile -t metropolis/mqtt-sensor:dev .
docker build -f testbeds/metropolis/devices/operations/scada/Dockerfile -t metropolis/scada:dev .
docker build -f testbeds/metropolis/devices/operations/hmi/Dockerfile -t metropolis/hmi:dev .
docker build -f testbeds/metropolis/devices/operations/historian/Dockerfile -t metropolis/historian:dev .
docker build -f testbeds/metropolis/devices/operations/engineering_workstation/Dockerfile -t metropolis/engineering:dev .
```

These create local Docker images. They do not create GNS3 templates or place nodes in a project.

### 3. Create the GNS3 templates and topology

In GNS3, create Docker templates that reference the built images, import/configure the VyOS appliance, then add the router, switch, and device nodes to a project. Configure the switch VLAN membership and trunk ports according to `testbeds/metropolis/switch/`; apply the router scripts to the matching VyOS nodes after checking interface numbering.

Use `testbeds/metropolis/datasets/water_treatment_v1/device_instances/initial_devices.yaml` as the initial node inventory. GNS3 does not read this YAML automatically; copy each node's `NODE_HOSTNAME`, `NODE_IP`, and `NODE_GATEWAY` values into its Docker environment settings. The device images now include a shared startup script that applies those values to `eth0` (or `NODE_INTERFACE`) before launching the application. Leave `NODE_IP` empty to skip address setup. GNS3 templates and topology creation, router startup, and switch setup are still manual; a future topology builder can read the inventory and populate node settings automatically.

### 4. Start and check the lab

Once node network settings and links have been configured, start the broker and controller before the MQTT clients and SCADA node. Check each node's console for startup or connection messages, confirm routing between the intended subnets, then capture traffic on selected GNS3 links. Record the topology, IPs, image versions, scenarios, capture points, and timestamps under the matching `testbeds/<testbed_id>/datasets/<name>_v<n>/` directory.

This sequence is a deployment plan, not a verified one-command runbook. Container image builds have not yet been validated on an Ubuntu GNS3 server, and the repository does not yet contain a Metropolis template/topology builder or capture/scenario orchestrator. GNS3 Docker node interfaces must be connected before the containers start so the entrypoint can find and configure them.

## Reusing Gotham scripts and tools

Gotham's reusable patterns include Docker-based device templates, GNS3 API helpers, router configuration scripts, topology construction, scenario orchestration, and packet capture. Metropolis is designed to adopt compatible parts of that workflow while using its own site layout and OT device models.

Gotham scripts that automate attacks or scenarios are usually tied to assumptions such as a Gotham project name, node-name patterns, target addresses, service ports, credentials, and installed GNS3 helper functions. Before using one with Metropolis, update those values to the Metropolis project and node inventory, verify that the target service exists in the selected subnet, and make sure the required route and lab firewall policy are in place. A Gotham MQTT attack script, for example, can only target a Metropolis MQTT broker if its configured address, port, and test credentials match that broker and the attack host can reach it. Attack scripts are not plug-and-play across the two topologies.

The current `scripts/run_gotham_pipeline.py` serves a different purpose: it runs feature cleaning, labeling, and preparation on the existing Gotham CSV files. It is separate from GNS3 scenario scripts and does not yet process Metropolis captures.

## Dataset layout

Each experiment should have a versioned dataset folder, for example `testbeds/metropolis/datasets/water_treatment_v1/`:

```text
testbeds/metropolis/datasets/water_treatment_v1/
├── topology/          # Sites, address plan, routers, switches, and links
├── device_instances/  # Named nodes, addresses, roles, and image settings
├── protocol_profiles/ # Modbus maps, MQTT topics, and security settings
├── scenarios/         # Normal operation and individual experiments
├── metadata/           # Capture points, labels, timestamps, and run details
└── captures/           # PCAPs; generated capture files are Git-ignored
```

Keep reusable device behavior under `testbeds/metropolis/devices/` and per-run assignments under the versioned dataset directory. Create a new dataset version rather than silently changing the configuration associated with an existing capture set.

## Python research package

The `metropolis` package is managed with Python 3.12+ and [uv](https://docs.astral.sh/uv/):

```bash
uv sync --group dev
uv run python -c "import metropolis; print(metropolis.__file__)"
uv run pytest
```

The import package is `metropolis` (the installable project distribution is `metropolis-ot-detection-systems`). It includes runtime configuration helpers and `metropolis.testbeds.load_address_plan()` for loading a bundled address plan as a Python mapping:

```python
from metropolis.testbeds import load_address_plan

plan = load_address_plan()
print(plan["testbed_id"], len(plan["networks"]))
```

The loader accepts `testbed_id` and `dataset_version` arguments for future testbeds and versions. Data ingestion, feature extraction for Metropolis traffic, provenance modeling, and evaluation workflows remain under development.

For a runnable example that imports the package and loads the v1 address plan into a pandas table, open [`examples/test_metropolis_import.ipynb`](examples/test_metropolis_import.ipynb) after syncing the development environment. The address plan is included in built distributions, so the loader works without a repository checkout.

To check the Metropolis address plan, device inventory, router interfaces/static routes, and switch VLAN specs against each other, run `uv run python scripts/validate_metropolis_topology.py`. Add `--report testbeds/metropolis/datasets/water_treatment_v1/topology/validation-report.md` to save a readable cross-reference.

## Project layout

```text
schemas/                                  Reusable testbed configuration schemas
testbeds/metropolis/                      Metropolis testbed assets
  router/                                 VyOS router configuration drafts
  switch/                                 GNS3 switch/VLAN specifications
  devices/                                Dockerfiles and Python device simulators
  datasets/                               Versioned topology, instances, scenarios, and captures
scripts/                                  Dataset processing utilities (currently Gotham data)
src/metropolis/                           Python research package
tests/                                     Tests for the current Python package
```

## Development

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv build
```

GitHub Actions runs lint, formatting checks, tests, package builds, and import checks for the Python package. These CI checks do not build or start the GNS3 Docker testbed.
