# Water treatment dataset v1

The reference network's settings and generated experiment data live here.
Run commands from the repository root.

## Configuration files

| File | Defines |
|---|---|
| [topology/address-plan.yaml](topology/address-plan.yaml) | Sites, VLANs, subnets, and gateways |
| [topology/links.yaml](topology/links.yaml) | Declared cabling and capture-point IDs |
| [topology/firewall-policy.yaml](topology/firewall-policy.yaml) | Permitted routed flows by network/CIDR, protocol, and port |
| [device_instances/initial_devices.yaml](device_instances/initial_devices.yaml) | Device names, addresses, images, and environments |
| [device_instances/infrastructure.yaml](device_instances/infrastructure.yaml) | Routers, switch port roles, and router-script paths |
| [protocol_profiles/modbus_actions_v1.yaml](protocol_profiles/modbus_actions_v1.yaml) | Cell-specific Modbus attack actions |

Other protocol settings currently live in device code and inventory. The
dataset's `scenarios/` and `metadata/` directories are reserved; the active
[attack catalog](../../../../scripts/attacks/scenarios/v1.json) is shared
under `scripts/attacks/`.

Router scripts configure OSPF, BFD, SNMP, and syslog. The simulated WAN is an
untrusted underlay with no site-to-site IPsec. Same-VLAN traffic bypasses the
routed firewall policy.

## Validate changes

```bash
uv run python scripts/validate_metropolis_topology.py
uv run python scripts/generate_switch_reference.py --write
uv run python scripts/gns3/run.py render_firewall --check
```

The validator checks declarations against schemas and cross-checks addresses,
links, router scripts, and switch roles. Mark deliberately uncabled devices
with `link_policy: intentionally_uncabled`. It does not prove live connectivity.

Regenerate the diagram after changing the topology:

```bash
uv run python -m metropolis.topology \
  --dataset testbeds/metropolis/datasets/water_treatment_v1 \
  draw --out docs/images/metropolis-topology.svg
```

## Experiment files

| Path under this dataset | Contents |
|---|---|
| `captures/<run-id>/` | Copied PCAPs and their capture manifest |
| `runs/<run-id>/manifest.json` | Configuration, collection results, and failures |
| `runs/<run-id>/timeline.jsonl` | Run actions and project status |
| `runs/<run-id>/attacks.jsonl` | Separately launched attack actions |
| `runs/<run-id>/logs/` | Syslog, normalized container diagnostics, and raw logs |
| `runs/<run-id>/features/` | Extracted/cleaned CSVs and processing reports |

Capture and run outputs are Git ignored. Archive them with their manifests
before changing the configuration used to produce them. Create another dataset
version when the experiment configuration needs a new identity.

Use the [GNS3 guide](../../../../scripts/gns3/README.md) to collect a run and
the [processing guide](../../../../scripts/README.md#dataset-processing-pipeline)
to extract its packet features.
