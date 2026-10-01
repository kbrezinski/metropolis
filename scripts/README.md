# Repository scripts

This directory contains dataset-processing and topology-validation utilities.
It does not contain attack execution scripts or an automated GNS3 topology
builder. See [`attacks/README.md`](attacks/README.md) for notes on adapting
Gotham scripts to the Metropolis nodes and services.

## GNS3 implementation references

Metropolis' GNS3 design is currently expressed as device models and
configuration specifications rather than a one-command topology deployment:

- Buildable Docker device models: `testbeds/metropolis/devices/`
- Router configuration scripts: `testbeds/metropolis/router/`
- Switch configuration notes: `testbeds/metropolis/switch/`
- Planned device instances: `testbeds/metropolis/datasets/water_treatment_v1/device_instances/initial_devices.yaml`
- Address and subnet plan: `testbeds/metropolis/datasets/water_treatment_v1/topology/address-plan.yaml`
- Consistency check: `python scripts/validate_metropolis_topology.py`

When creating a GNS3 project, use stable logical roles for equivalent
experiment components across testbeds. For example, an attacker node could be
named `LAB-ATTACKER-01`, while the Metropolis MQTT broker is
`MET-MQTT-BROKER-01`. Record the mapping, actual project name, node names,
addresses, image versions, and routes with each experiment.

The topology validator checks the checked-in plans; it does not connect to
GNS3 or confirm that a running project matches those plans. Start and validate
nodes and routes in GNS3 before capturing traffic.

## Dataset-processing pipeline

[`run_gotham_pipeline.py`](run_gotham_pipeline.py) prepares downloaded Gotham
network packet feature CSVs. It is separate from the Metropolis GNS3 topology
and from attack execution. See the root README for its inputs and outputs.
