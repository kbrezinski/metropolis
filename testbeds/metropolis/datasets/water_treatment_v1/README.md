# Water treatment dataset v1

The reference network's settings and generated experiment data live here.
Run commands from the repository root.

## Configuration files

| File | Defines |
|---|---|
| [topology/address-plan.yaml](topology/address-plan.yaml) | Sites, VLANs, subnets, and gateways |
| [topology/links.yaml](topology/links.yaml) | Physical cabling/capture-point IDs and logical IPsec overlays |
| [topology/firewall-policy.yaml](topology/firewall-policy.yaml) | Permitted routed flows by network/CIDR, protocol, and port |
| [topology/resource-baseline.yaml](topology/resource-baseline.yaml) | Optional shared container CPU, RAM, and explicit swap limits, with model/image overrides |
| [device_instances/initial_devices.yaml](device_instances/initial_devices.yaml) | Device names, addresses, images, and environments |
| [device_instances/infrastructure.yaml](device_instances/infrastructure.yaml) | Routers, switch port roles, and router-script paths |
| [protocol_profiles/modbus_actions_v1.yaml](protocol_profiles/modbus_actions_v1.yaml) | Cell-specific Modbus attack actions |

Other protocol settings currently live in device code and inventory. The
dataset's `scenarios/` and `metadata/` directories are reserved; the active
[attack catalog](../../../../scripts/attacks/scenarios/v1.json) is shared
under `scripts/attacks/`.

Router scripts configure OSPF, BFD, SNMP, syslog, and IKEv2/IPsec. `MET-OT-WAN`
remains the edge firewall and tunnel hub for both remote sites. The untrusted
WAN carries IPsec; inter-site OSPF/BFD and application flows use the VTI
overlays. See [IPsec setup](../../../../scripts/gns3/README.md#site-to-site-ipsec)
for controller-side keys and live verification steps. Same-VLAN traffic
bypasses the routed firewall policy.

The DMZ RDP host also runs the telemetry relay and receives selected SCADA
status over mutual TLS; the enterprise reporting client reads it over HTTPS.
Both use the existing subnets
and TCP 443 permissions in the firewall policy. Peer names and roles are in the
inventory; the builder resolves their addresses. See the
[setup and capture points](../../../../scripts/gns3/README.md#dmz-and-enterprise-data-path).

The existing historian also subscribes to SCADA over OPC UA on TCP 4840,
alongside MQTT. Reciprocal inventoried peer names determine application
identities and the endpoint. Signed and encrypted sessions are the default;
plaintext requires explicit matching settings on both peers. See the
[OPC UA guide](../../../../scripts/gns3/README.md#opc-ua-between-scada-and-the-historian).

The resource baseline is applied only by the explicit
[`configure_baseline --apply` setup command](../../../../scripts/gns3/README.md#container-resource-baseline).
The bundled starting limits are 1 CPU core and 512 MiB RAM, with no additional
swap and a 1 GiB SCADA override. They are editable ceilings, not reserved
resources or validated device requirements. Experiments check the optional
file without enforcing it and record `resource_baseline` status and drift in
the run manifest. Model overrides follow shared defaults; exact image
overrides take precedence. VyOS QEMU appliances are reported separately.

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
| `runs/<run-id>/profiles.json` | Selected optional limits, original settings, readback, and restoration state |
| `runs/<run-id>/timeline.jsonl` | Run actions and project status |
| `runs/<run-id>/attacks.jsonl` | Separately launched attack actions |
| `runs/<run-id>/logs/` | Syslog, normalized container diagnostics, and raw logs |
| `runs/<run-id>/metrics/` | CPU/RAM samples, per-link traffic rates, summary, and charts |
| `runs/<run-id>/features/` | Extracted/cleaned CSVs and processing reports |

Capture and run outputs are Git ignored. Archive them with their manifests
before changing the configuration used to produce them. Create another dataset
version when the experiment configuration needs a new identity.

Use the [GNS3 guide](../../../../scripts/gns3/README.md) to collect a run and
the [processing guide](../../../../scripts/README.md#dataset-processing-pipeline)
to extract its packet features.

Experiment metrics are enabled by default. CPU/RAM is sampled every five
seconds; traffic rates reuse the PCAPs in one-second intervals per captured
link. These observations do not apply device limits or degrade links. See the
[metrics guide](../../../../scripts/gns3/README.md#cpu-memory-and-traffic-reports)
for options, file formats, missing-source reporting, and compute limitations.
