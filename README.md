# Metropolis

**A GNS3 water-treatment OT testbed and Python topology library**

[![CI](https://github.com/kbrezinski/metropolis/actions/workflows/ci.yml/badge.svg)](https://github.com/kbrezinski/metropolis/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/downloads/)

Metropolis simulates controllers, instruments, actuators, operator interfaces,
and network services. You can build the declared network, generate normal and
attack traffic, and save PCAPs and logs for each experiment.

The Python package provides topology loading, editing, validation, and drawing.
Detection models and training are early-stage work.

## Start here

| I want to… | Guide |
|---|---|
| Build and run the GNS3 testbed | [GNS3 setup and experiments](scripts/gns3/README.md) |
| Use the Python library | [Install and try the library](#install-and-try-the-library) |
| Run an attack scenario | [Attack toolkit](scripts/attacks/README.md) |
| Extract or clean PCAP features | [Processing scripts](scripts/README.md#dataset-processing-pipeline) |
| Change devices or protocol settings | [Device reference](testbeds/metropolis/devices/README.md) |
| Find topology settings and run files | [Dataset guide](testbeds/metropolis/datasets/water_treatment_v1/README.md) |
| Review declared network security | [Codex Security review](scripts/security/README.md) |
| Connect the external REA tool | [REA guide](docs/rea.md) |

The GNS3 guide contains the complete setup sequence. Python installation alone
does not build images or create a GNS3 project.

## Install and try the library

Install [uv](https://docs.astral.sh/uv/), then run:

```bash
git clone https://github.com/kbrezinski/metropolis.git
cd metropolis
uv sync --group dev
```

Load the bundled address plan:

```python
from metropolis.testbeds import load_address_plan

plan = load_address_plan()
print(plan["testbed_id"])
print(plan["networks"])
```

From the repository root, inspect the topology without a GNS3 server:

```bash
uv run python -m metropolis.topology check
uv run python -m metropolis.topology --help
```

The import name is `metropolis`; the distribution name is
`metropolis-ot-detection-systems`. Bundled topology data is included in built
packages. GNS3 and attack commands run from this source checkout.

## Network overview

The reference dataset contains four plant cells—intake, dosing, filtration, and
disinfection—and two remote RTU sites. SCADA polls controllers over Modbus TCP;
local actuators use cyclic EtherNet/IP. MQTT connects supervisory services and
HMIs. Routers use OSPF and BFD, with a declared firewall policy between networks.

[![Metropolis topology](docs/images/metropolis-topology.svg)](docs/images/metropolis-topology.svg)

The diagram represents declared configuration. Isolated nodes have no declared
link. See the [dataset guide](testbeds/metropolis/datasets/water_treatment_v1/README.md)
for the files that define the network.

## Repository layout

| Directory | Contents |
|---|---|
| `src/metropolis/` | Python package |
| `testbeds/` | Versioned network settings, device models, and router configuration |
| `scripts/` | GNS3 automation, attacks, security review, and packet processing |
| `schemas/` | JSON Schemas for configuration and run records |
| `tests/` | Unit and tooling tests |
| `PLANS.md` | Priorities and future work |

## Development

Run from the repository root after installing the development dependencies:

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
uv run python scripts/validate_metropolis_topology.py
uv run python scripts/generate_switch_reference.py --check
uv build
```

Unit tests check reusable behavior. The standalone topology validator checks
dataset declarations. Neither establishes that a running GNS3 network carries
the intended traffic; verify that in your GNS3 environment.

Current limits:

- Device values are simplified lab models, with no full physical process simulation.
- Site-to-site IPsec and CoAP DTLS are not configured. MQTT TLS is optional.
- Experiments collect traffic and logs; launch attack commands separately.
- PCAPs and network-delivered syslog support research telemetry. Container output
  is retained as simulator diagnostics.

## Credits and citation

Metropolis builds on the device, topology, and scenario ideas of the
[Gotham IoT Testbed](https://github.com/xsaga/gotham-iot-testbed), described in
[its research paper](https://doi.org/10.1109/TDSC.2023.3247166).
Follow upstream licenses and attribution requirements when reusing that work.

```bibtex
@misc{brezinski2026metropolis,
  author       = {Brezinski, Kenneth and Hu, Jiankun},
  title        = {Metropolis: {OT} Detection Systems},
  year         = {2026},
  howpublished = {GitHub},
  url          = {https://github.com/kbrezinski/metropolis}
}
```
