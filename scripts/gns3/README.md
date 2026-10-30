# Build and run the GNS3 testbed

Use this guide for the complete path from a source checkout to an experiment.
Commands run from the repository root in **Ubuntu/Bash**. Each command also
accepts `--help`.

## 1. Prepare the controller and compute

You need:

- Python 3.12 managed with uv: run `uv sync --group dev`.
- A running GNS3 controller and a compute with Docker.
- An imported, bootable VyOS 1.3 appliance named `VyOS 1.3.0`. Install its
  operating system interactively if the appliance is blank.
- Docker CLI access to the compute. The Open vSwitch image also needs kernel
  Open vSwitch support and GNS3's container network permissions.

The defaults are project `metropolis_water_treatment_v1`, compute `local`,
and dataset `testbeds/metropolis/datasets/water_treatment_v1`.

### Controller and Docker connections

The controller API and the compute's Docker engine are separate connections.
GNS3 settings are read from `gns3_server.conf` under
`~/.config/GNS3/<version>/` on Linux or `%APPDATA%\GNS3\<version>\` on Windows.
Environment variables override those settings. For a local controller:

```bash
export GNS3_SERVER_HOST=127.0.0.1
export GNS3_SERVER_PORT=3080
```

Use `GNS3_SERVER_USERNAME` and `GNS3_SERVER_PASSWORD` if authentication is
required. Command options `--host`, `--port`, `--username`, and `--password`
override the connection for one invocation.

Choose the Docker connection for the examples below:

```bash
COMPUTE_DOCKER=local=unix:///var/run/docker.sock
# For a remote compute instead:
# COMPUTE_DOCKER=local=ssh://gns3@gns3-vm
```

Replace the left side of the mapping with the compute ID from GNS3 if it is
not `local`. The right side is that compute's Docker URI. SSH access must use
keys or an agent; password-bearing URIs and unauthenticated TCP endpoints are
rejected. Repeat `--docker-host` for multiple computes.

## 2. Build the images on the compute

Clone the repository onto every compute that will host its images. Run the
following commands from the repository root **on that compute**.

Generate the telemetry certificate bundle before building SCADA, the DMZ relay,
or the enterprise client:

```bash
uv run python scripts/gen_lab_certificates.py --service telemetry \
  --dataset testbeds/metropolis/datasets/water_treatment_v1
```

Use the same ignored `certs/telemetry/` bundle on all hosting computes. The
relay image receives its server key; SCADA receives only the writer key; the
enterprise client receives only the reader key. All three receive the CA
certificate. The CA key is never saved. Keep these lab images private. Changing
node names, the relay address, or certificates requires rebuilding these images
from a newly shared bundle and refreshing the affected GNS3 nodes.

Before building the two Merlin images, generate their lab certificate bundle
from the selected dataset:

```bash
uv run python scripts/gen_lab_certificates.py --service merlin \
  --dataset testbeds/metropolis/datasets/water_treatment_v1
```

The generated `certs/merlin/` files are Git ignored. The C&C image receives
the server certificate and key; the agent image receives only the CA
certificate. If those images are built on different computes, use the **same**
generated bundle on both computes. Generating separate bundles makes the
agent reject the C&C. For another dataset, substitute its directory in the
command and rebuild both images. Do not publish images containing the lab
server key.

```bash
DEVICES=testbeds/metropolis/devices
docker build -f "$DEVICES/controllers/plc/Dockerfile" -t metropolis/controller:dev .
docker build -f "$DEVICES/field/actuators/Dockerfile" -t metropolis/actuator:dev .
docker build -f "$DEVICES/field/analyzers/Dockerfile" -t metropolis/analyzer:dev .
docker build -f "$DEVICES/field/instruments/Dockerfile" -t metropolis/dp-instrument:dev .
docker build -f "$DEVICES/field/level_instrument/Dockerfile" -t metropolis/level-instrument:dev .
docker build -f "$DEVICES/field/sensors/Dockerfile" -t metropolis/mqtt-sensor:dev .
docker build -f "$DEVICES/services/mqtt_broker/Dockerfile" -t metropolis/mqtt-broker:dev .
docker build -f "$DEVICES/operations/scada/Dockerfile" -t metropolis/scada:dev .
docker build -f "$DEVICES/services/telemetry_relay/Dockerfile" -t metropolis/telemetry-relay:dev .
docker build -f "$DEVICES/operations/enterprise_client/Dockerfile" -t metropolis/enterprise-client:dev .
docker build -f "$DEVICES/operations/hmi/Dockerfile" -t metropolis/hmi:dev .
docker build -f "$DEVICES/operations/historian/Dockerfile" -t metropolis/historian:dev .
docker build -f "$DEVICES/operations/engineering_workstation/Dockerfile" -t metropolis/engineering:dev .
docker build -f "$DEVICES/operations/rdp_server/Dockerfile" -t metropolis/rdp-server:dev .
docker build -f "$DEVICES/operations/rdp_client/Dockerfile" -t metropolis/rdp-client:dev .
docker build -f "$DEVICES/operations/control_client/Dockerfile" -t metropolis/control-client:dev .
docker build -f "$DEVICES/field/legacy_gateway/Dockerfile" -t metropolis/legacy-gateway:dev .
docker build -f "$DEVICES/services/dns/Dockerfile" -t metropolis/dns:dev .
docker build -f "$DEVICES/services/ntp/Dockerfile" -t metropolis/ntp:dev .
docker build -f "$DEVICES/infrastructure/openvswitch/Dockerfile" -t metropolis/openvswitch:dev .
docker build -f "$DEVICES/field/reachability_probe/Dockerfile" -t metropolis/reachability-probe:dev .
docker build -f "$DEVICES/attack/mirai/cnc/Dockerfile" -t metropolis/mirai-cnc:dev .
docker build -f "$DEVICES/attack/mirai/scan_listener/Dockerfile" -t metropolis/mirai-scan-listener:dev .
docker build -f "$DEVICES/attack/mirai/loader/Dockerfile" -t metropolis/mirai-loader:dev .
docker build -f "$DEVICES/attack/mirai/wget_loader/Dockerfile" -t metropolis/mirai-wget-loader:dev .
docker build -f "$DEVICES/attack/mirai/bot/Dockerfile" -t metropolis/mirai-bot:dev .
docker build -f "$DEVICES/attack/merlin/cnc/Dockerfile" -t metropolis/merlin-cnc:dev .
docker build -f "$DEVICES/attack/merlin/agent/Dockerfile" -t metropolis/merlin-agent:dev .
```

This builds the bundled image set. For a changed inventory, build every image
it names and add a template registry entry for any new image. The offline
registry check compares declarations; it does not inspect Docker images.

## 3. Validate, register, and build

Back on the machine controlling GNS3, inspect the declarations:

```bash
uv run python scripts/validate_metropolis_topology.py
uv run python scripts/generate_switch_reference.py --check
uv run python scripts/gns3/run.py register_templates --check
uv run python scripts/gns3/run.py build_topology --plan
uv run python scripts/gns3/run.py render_firewall --check
```

These commands need no live server. Read any `skipped` lines in the build plan.

Then register Docker templates and build the project:

```bash
uv run python scripts/gns3/run.py register_templates
uv run python scripts/gns3/run.py build_topology --docker-host "$COMPUTE_DOCKER"
```

The builder creates nodes and links, starts switches, applies VLAN access/trunk
settings, and checks the switch port state. If your router template or compute
has another name, supply `--router-template NAME` or `--compute-id ID` to the
build command; use `--dataset PATH` for another versioned dataset. Reused
Docker nodes keep their original environment. The builder reports stale
MQTT/DNP3/CoAP/SNMP settings without printing credentials; refresh or recreate
those nodes before a run, then rebuild their images on the hosting compute.
The router scripts target VyOS 1.3; changing a template name
does not make them compatible with another VyOS release.

## 4. Start and configure

Set the two controller-side [IPsec keys](#site-to-site-ipsec) before configuring
the reference routers. Planning and offline validation need no keys.

```bash
uv run python scripts/gns3/run.py lab_lifecycle --start
uv run python scripts/gns3/run.py build_topology --configure-routers \
  --docker-host "$COMPUTE_DOCKER"
```

Startup orders infrastructure, field devices, controllers, services, and polling
clients. The router loader applies interface, OSPF, BFD, SNMP, syslog, IPsec,
and generated firewall settings, then checks the reported configuration.

The router console defaults are `vyos` / `vyos`. If changed, use the builder's
`--router-username` and `--router-password` options. For `experiment_run`,
the password override is the `METROPOLIS_ROUTER_PASSWORD` environment variable.

Check cabling, VLAN isolation, router neighbors, permitted/blocked paths, and
service startup in your GNS3 environment. Offline checks validate declarations.

### DMZ and enterprise data path

The reference dataset adds `MET-TELEMETRY-RELAY-01` (`10.20.40.30`) in the DMZ
and `MET-ENTERPRISE-REPORTING-01` (`10.30.10.10`) on the enterprise switch.
SCADA posts one selected status/alarm snapshot after each polling cycle; the
enterprise client reads the latest snapshot every ten seconds. Both connections
terminate at the relay on TCP 443. The policy permits SCADA and enterprise
subnets to reach that service; other routed traffic remains subject to default
drop. It adds no enterprise-to-controller permission.

Mutual TLS identifies the writer and reader separately. The reader can only
read; it cannot publish or issue control commands. The builder derives peer
URLs from `TELEMETRY_RELAY_NODE` in the selected inventory and reports stale
`TELEMETRY_` settings on reused nodes. Rebuild and recreate/refresh existing
SCADA nodes to activate the new image and environment; name reuse alone does
not update either.

Capture `attach-scada`, `attach-telemetry-relay`, `attach-enterprise-reporting`,
`link-core-dmz`, `link-dmz-switch`, and `link-enterprise-switch` to observe the
reference paths. Default all-link capture includes these points. HTTPS PCAPs
show encrypted exchanges and timing; they do not expose the JSON payloads.
Relay/client stdout records receipt, authorization failures, and transport
errors as container diagnostics in the usual run logs. Those logs are not
network-delivered syslog. A successful container start does not prove delivery.

For live checks, confirm the enterprise client receives current snapshots,
rejects an untrusted relay certificate, and cannot publish with its reader
identity. Stop the relay and confirm SCADA keeps polling, then restart it and
check recovery. See the [device settings and limits](../../testbeds/metropolis/devices/README.md#dmz-telemetry-relay-and-enterprise-reader).

To give containers shared CPU/RAM limits, apply the optional
[resource baseline](#container-resource-baseline) after startup and before
selecting an experiment profile.

## 5. Collect an experiment

On a running lab, choose a **new run ID**:

```bash
uv run python scripts/gns3/run.py experiment_run \
  --run-id baseline_001 --duration 120 --docker-host "$COMPUTE_DOCKER"
```

Defaults: capture every built, capturable physical link for 60 seconds, collect
container diagnostics and syslog, sample CPU/RAM every five seconds, and
generate a run ID if omitted. Traffic reports reuse the completed PCAPs.

| Option | Effect |
|---|---|
| `--links ID ...` | Capture only these declared links |
| `--start-lab` | Begin log collection before starting nodes |
| `--configure-routers` | Apply router settings after startup |
| `--dataset PATH` | Read a different dataset for the run |
| `--project NAME` | Use a different GNS3 project |
| `--metrics-interval SECONDS` | Set the CPU/RAM sampling interval; default 5, positive and finite |
| `--no-metrics` | Disable resource sampling and traffic reports for this run |
| `--profile PATH` | Explicitly apply a resource/link profile for this run, then restore its original settings |
| `--rdp-udp-amplify NODE` | Repeat to temporarily enable the synthetic UDP 3389 amplification mode on a declared RDP server |
| `--mqtt-acl-probe SOURCE:TARGET` | After capture starts, test one HMI's own topic and another HMI cell's command topic from the source HMI node |

The MQTT check is opt-in. For the bundled dataset, select two distinct HMI
names, for example `--mqtt-acl-probe MET-HMI-INTAKE-01:MET-HMI-DOSING-01`.
It sends inert payloads that SCADA will not treat as commands. Success requires
broker log evidence of the own-topic publish and cross-cell denial, SCADA log
evidence that it received the own-topic marker but not the cross-cell marker,
and saved HMI, broker, and SCADA attachment PCAPs. A publish acknowledgement
alone does not establish authorization. The run manifest and `attacks.jsonl`
record the source, target, UTC times, capture links, result, and failures
without passwords. Missing broker or SCADA evidence makes the check fail.

When the selected dataset declares RDP clients, `experiment_run` starts one
authenticated RDP session per routed client/server pair after captures start.
Each session lasts up to 30 seconds and sends small synthetic UDP 3389 probes
alongside the real TCP desktop session. The reference paths are engineering
to the OT DMZ and operations to engineering. Capture both endpoint attachment
links and the routed uplinks to verify their packet origins. Session and mode
failures are recorded with the run; a missing source or capture is not evidence
of successful RDP traffic.
The run report derives these paths from the unique shortest declared cabling
path; inspect the PCAPs to confirm the live routed path.
The headless client accepts the server's self-signed TLS certificate without
verifying its identity, so these sessions do not establish server authenticity.
Use a capture duration of at least 30 seconds to observe a full session.
For the bundled dataset, the endpoint links are `attach-engineering`,
`attach-rdp-dmz`, `attach-ops-admin`, and `attach-rdp-engineering`; the related
uplinks include `link-ops-switch`, `link-core-dmz`, `link-dmz-switch`,
`link-ops-scada-uplink`, and `link-ops-engineering-uplink`.

The UDP service is **not RDP-over-UDP**. Its default 16-byte request/reply is
a lab marker. Opt-in amplification returns one 512-byte reply to the imported
UDP probe while the selected run is active; the mode is restored afterward and
expires automatically if the controller disappears. This does not launch the
separate `scripts/attacks/rdp/` example or establish a Windows RDP
vulnerability. A run using that example also needs a temporary
`attack-lab:ot-dmz:udp:3389` or `attack-lab:engineering:udp:3389` firewall
exception, selected for its target network.
Run only one experiment at a time in a GNS3 project: concurrent runs could
change each other's temporary UDP mode.

To collect startup logs, use `--start-lab --configure-routers` on a stopped lab.
Nodes remain running when collection finishes. Launch any
[attack scenario](../attacks/README.md#record-an-attack-with-a-capture) separately
with the same run ID, then stop the lab when finished:

```bash
uv run python scripts/gns3/run.py lab_lifecycle --stop
```

Topology building, capture, log, experiment, and firewall commands apply
`--dataset`. Lifecycle accepts `--dataset` but does not apply it yet.
Registration can select `--inventory`.

## Capture coverage and files

Inspect coverage before selecting capture points:

```bash
uv run python scripts/gns3/run.py capture_traffic --coverage
```

For PCAPs without log collection:

```bash
uv run python scripts/gns3/run.py capture_traffic --all-built --duration 60
# Or capture selected declared links:
uv run python scripts/gns3/run.py capture_traffic \
  --links link-core-plant attach-intake-plc --duration 60
```

`--list` lists declared IDs offline. `--coverage` uses GNS3 to resolve node
identities **and adapter/port numbers** and reports missing, ambiguous, or
miswired links. `--all-built` selects available physical links, including
undeclared links labeled `gns3_<link-id>`; uncabled declarations are unavailable.
Logical IPsec overlays are reported separately, create no GNS3 cables, and
cannot be selected as capture points or link-profile targets.

Capture host attachments for local subnet exchanges and uplinks for routed
traffic. A router uplink does not see traffic switched within one VLAN.
Selection alone does not prove complete traffic coverage.
On remote-site WAN links, expect IKE/ESP or NAT-T traffic. Capture
`attach-scada`, the RTU attachments, and `link-core-wan` to see application
traffic before or after encryption; WAN PCAPs do not expose decrypted payloads.

PCAPs are retrieved into `captures/<run-id>/` under the selected dataset.
Each must contain a complete packet. Its manifest records declared/GNS3 link
IDs, UTC times, server and local paths, packet counts, hashes, and failures.
The original remains at GNS3's returned `capture_file_path`. Failed starts,
stops, retrievals, and empty files make the run incomplete. Cleanup stops
captures that successfully started, including after errors or interruption.

## Experiment device logs

All experiment files share one run ID:

| Dataset-relative path | Contents |
|---|---|
| `captures/<run-id>/manifest.json` | PCAP results, coverage, and hashes |
| `runs/<run-id>/manifest.json` | Run configuration, source status, and failures |
| `runs/<run-id>/timeline.jsonl` | Orchestration actions and project status |
| `runs/<run-id>/attacks.jsonl` | Attack actions and optional HMI-origin MQTT ACL check |
| `runs/<run-id>/logs/devices.jsonl` | Normalized container diagnostics |
| `runs/<run-id>/logs/syslog.jsonl` | Router syslog and bridged switch events |
| `runs/<run-id>/logs/raw/` | Original container lines, with secret redaction |
| `runs/<run-id>/metrics/` | Resource samples, traffic rates, summary, and charts |

| Source | Collection path and limits |
|---|---|
| VyOS routers | UDP 514 to the lab's NTP/syslog receiver; UDP may lose messages |
| Open vSwitch | Daemon events carried through Docker stdout, marked `docker_stdout_bridge`; no network syslog management interface |
| Docker devices/services | stdout/stderr from the compute hosting the container |
| Reachability probes | Intentionally silent; absence of service logs is expected |

Network-delivered syslog and PCAPs are research telemetry; container output
supports simulator diagnosis. Missing sources and collector errors remain
visible in the manifest. Direct Docker access is needed on each compute;
the GNS3 controller does not relay container logs.

For log collection without PCAPs, use `collect_device_logs` with
`--run-id`, `--duration`, and the same Docker mappings. Start collection before
the lab to retain startup lines. The collector follows new containers and
requests logs since collection began, subject to Docker rotation.

Records have collector UTC timestamps, separate device timestamps when present,
and structured fields in `attributes`. Plain text, malformed JSON, and
continuation lines are retained. Common secret assignments and private-key
blocks are redacted; review arbitrary log text before sharing.

Record formats: [run schema](../../schemas/experiment-run.schema.json) and
[event schema](../../schemas/device-log-event.schema.json).

## CPU, memory, and traffic reports

`experiment_run` starts read-only resource collection before optional lab
startup and stops it on completion, errors, or interruption. It discovers
containers from GNS3 and connects to the Docker engine on each node's compute
using the same `--docker-host` mappings as log collection, including remote
computes. The Docker SDK is installed by `uv sync --group dev`.
SSH metrics use keys or an agent and require a trusted host key in the user's
known-hosts file. They read ordinary SSH host, user, port, and identity settings;
`ProxyCommand` is unsupported for resource metrics. Remote sampling has bounded
worker processes so an unresponsive compute cannot hold up cleanup indefinitely.

| File under `runs/<run-id>/metrics/` | Contents |
|---|---|
| `containers.jsonl` | Incremental container CPU, memory, identity, and existing resource limits |
| `computes.jsonl` | Incremental overall compute CPU/RAM and available capacity information |
| `bandwidth.csv` | Packets, original packet bytes, packets/s, and bits/s per captured link and time interval |
| `summary.json` | Measurement coverage, summaries, output paths, and failures |
| `charts/` | Named CPU/RAM and traffic charts generated after collection |

Samples carry the shared run ID and UTC time. Container CPU at 100% means one
fully used CPU core and can exceed 100% across several cores. Compute CPU is
the overall percentage reported by GNS3. Memory records preserve raw usage,
cache-adjusted working set, and the reported limit. Existing CPU/memory limits
are observed without changing them. Container CPU needs two readings; its first
sample is unavailable. Collection does not add network traffic
inside the testbed or apply bandwidth, delay, jitter, packet-loss, or device
resource restrictions.

Bandwidth is **observed traffic rate**, derived after the run from successful
PCAP copies in one-second intervals. Each capture point remains separate:
the same packet can appear on several links, so their rates are not summed into
a global network total. First and last intervals use their actual captured
duration when packet timestamps fit the recorded capture window. Small clock
differences use clearly marked estimated whole-second bounds; a discrepancy
above 60 seconds fails that link's report. Rates use original packet lengths,
excluding physical-layer overhead; they describe capture coverage, not link capacity or proof that no
packets were missed. Charts identify each compute, container, or captured link.

Missing readings remain unavailable rather than becoming zero. Container
restarts retain the new container identity. Unsupported nodes, collection
failures, and report failures are recorded in the run manifest; incremental
resource records remain available after a failed run. The
[resource sample schema](../../schemas/resource-metric.schema.json) describes
both container and compute records.

VyOS QEMU appliances have no Docker container resource sample. Their work,
Open vSwitch kernel forwarding, capture, and other processes may contribute to
overall compute usage. High utilization can indicate possible overload; it
does not establish what caused a slowdown. Unavailable GNS3 compute readings
are reported explicitly. Tests use mocked services and PCAP fixtures; verify
actual collection, attribution, and overhead in your GNS3 environment.

## Container resource baseline

The selected dataset's `topology/resource-baseline.yaml` is the single place
for normal container CPU, RAM, and swap limits. The bundled
[baseline file](../../testbeds/metropolis/datasets/water_treatment_v1/topology/resource-baseline.yaml)
starts with 1 CPU core and 512 MiB RAM per container. Its memory-plus-swap
limit is also 512 MiB, allowing no additional swap. SCADA has a 1 GiB override.
These are editable starting ceilings, not reserved resources or validated
device sizing requirements. VyOS QEMU appliances are outside this baseline.

Edit `defaults` for shared settings and `overrides.models` or
`overrides.images` for exceptions. Resolution is defaults, then model, then
exact image; an image override takes precedence. RAM and memory-plus-swap
values must be explicit in the resolved settings. See the
[baseline schema](../../schemas/resource-baseline.schema.json).

Inspect the resolved settings without Docker or GNS3:

```bash
uv run python scripts/gns3/run.py configure_baseline --plan
```

After starting the lab, apply the baseline to its containers:

```bash
uv run python scripts/gns3/run.py configure_baseline --apply \
  --docker-host "$COMPUTE_DOCKER"
```

This deliberately establishes persistent setup limits and checks their
readback. It does not recreate or start nodes, and it does not roll the
baseline back when an experiment finishes. Repeating it skips matching
settings. Missing containers and partial failures are reported; fix or start
the affected nodes and rerun. A stopped container with an existing container
ID can still receive its settings. Reapply after containers are recreated.

Check the live lab without changing it:

```bash
uv run python scripts/gns3/run.py configure_baseline --check \
  --docker-host "$COMPUTE_DOCKER"
```

`--check` is the default. Use `--dataset PATH`, `--project NAME`, and the usual
compute mappings for another lab. Each dataset has one baseline file at
`topology/resource-baseline.yaml`.

When that optional dataset file exists, `experiment_run` checks it after
optional startup and before applying an experiment profile. It records the
baseline path, hash, node settings, and differences in the manifest's
`resource_baseline` section, and reports `baseline_status` as `matched`,
`drift`, or `failed`. This check does not enforce limits or block capture
because of drift. A dataset without the file keeps the existing behavior.
Temporary profiles still restore the actual settings recorded before the run.

These checks establish configured limits, not demonstrated resource capacity.
Verify operation and sizing in your GNS3 environment.

## Optional device and link profiles

Profiles deliberately constrain selected devices and communication links.
Without `--profile`, experiment behavior and measurement-only reporting stay
unchanged. Enforcement uses separate tooling from the read-only collector.

Copy the [example YAML](profiles/constrained-field.example.yaml), replace its
placeholder node name and declared link ID with selectors from your selected
dataset, then run:

```bash
uv run python scripts/gns3/run.py experiment_run \
  --run-id constrained_001 --duration 120 --profile /path/to/profile.yaml \
  --docker-host "$COMPUTE_DOCKER"
```

| Setting | Meaning |
|---|---|
| `devices[].node` | Declared Docker device or switch name; addresses are resolved from the dataset |
| `cpus` | CPU allowance in cores, at least `0.01`, such as `0.5` |
| `memory_bytes` | RAM limit in bytes; at least 6 MiB |
| `links[].link` | Declared link ID, resolved to both GNS3 endpoint identities and port numbers |
| `bandwidth_kbps` | Per-direction bandwidth ceiling in decimal kilobits/s |
| `delay_ms`, `jitter_ms` | Added latency and variable delay, in milliseconds |
| `loss_percent` | Random packet-loss percentage per direction |

The [profile schema](../../schemas/run-profile.schema.json) requires at least
one selected device or link and one setting for each entry. Unknown targets,
duplicate targets, unknown keys, and invalid numeric settings are rejected.
CPU/RAM limits require Docker access to the target's compute. Reversible RAM
changes require a finite existing memory limit; the runner rejects an unlimited
baseline rather than silently keeping a new limit or recreating the container.
For RAM profiles, first apply the [resource baseline](#container-resource-baseline).
Size it for the simulator's normal operation. The run temporarily applies its
selected RAM limit and restores the recorded settings afterward. Baseline
preparation is optional and is never applied by default. CPU and link profiles
do not require a RAM baseline.

GNS3 link filters provide delay, jitter, and loss. Actual bandwidth control
requires a port on the repo-owned Open vSwitch image: ingress policing limits
traffic entering that port, while Linux HTB shaping limits traffic leaving it.
Policing can drop excess traffic. Rebuild the Open vSwitch image using the
[image-build instructions](#2-build-the-images-on-the-compute) to install its
profile helper and required tools. Existing switch containers must be recreated
or redeployed to use the rebuilt image; the builder reuses existing nodes.
A bandwidth profile on a link without a
supported switch endpoint fails explicitly. Existing custom switch QoS is not
overwritten.
Only an empty/default `noqueue` kernel queue is supported; existing custom
queue disciplines are rejected before any profile settings are applied.

Every target is checked and its original settings are saved in
`runs/<run-id>/profiles.json` before enforcement. The run records selected
profile identity, hash, configuration readback, failures, and restoration.
Original settings are restored when the run ends, including on errors and
interruption. Retain the state file for recovery if the controller process is
killed before cleanup.
Unlimited CPU capacity is restored with Docker's equivalent unlimited quota;
its zero-valued metadata can become a period of 100000 and quota of -1.
Restored values and any such metadata differences remain in the state file.

For manual recovery, use the matching project and compute connections:

```bash
uv run python scripts/gns3/run.py restore_profile \
  --state /path/to/run/profiles.json --docker-host "$COMPUTE_DOCKER"
```

Configuration readback verifies the requested settings, not the achieved
communication conditions. Existing CPU/RAM and PCAP reports provide observed
usage and throughput; low offered traffic cannot establish a bandwidth ceiling,
and one capture point cannot establish added delay, jitter, or packet loss.
The runner does not generate stress loads or active probe traffic. Compare
observed conditions with a baseline while checking overall compute load;
overload can confound the result. Live achieved-limit checks remain part of
your GNS3 verification.

## Routed firewall policy

The dataset's `topology/firewall-policy.yaml` permits flows by source network,
destination network, protocol, and port. Selectors accept address-plan network
IDs, `vlan-N` aliases, or CIDRs contained in a declared LAN. Permissions cover
all hosts in those networks. Router-originated syslog uses `router/NAME`.

The generated policy drops other new routed traffic, allows established
replies, and includes router-local zones. Same-VLAN traffic bypasses it.
Router scripts declare OSPF area 0 with passive device LANs, BFD on peer links,
and read-only SNMP polling from the control client. Inter-site OSPF/BFD uses
IPsec VTI interfaces; the physical WAN is not advertised into OSPF.

`render_firewall --check` validates the declared paths; omitting `--check`
prints the resolved flows and VyOS commands. In GNS3, inspect
`show ip ospf neighbor`, `show ip route`, and `show protocols bfd peer`,
and test both allowed and blocked application paths.

For a run-scoped exception from the attack network:

```bash
uv run python scripts/gns3/run.py experiment_run \
  --configure-routers --duration 120 \
  --attack-allow attack-lab:plant-intake:tcp:502 \
  --docker-host "$COMPUTE_DOCKER"
```

Use `-` as the ICMP port; repeat the option for additional flows. The source
must be inside `attack-lab` and the destination in another declared LAN.
Exceptions are recorded and the baseline is restored on completion or failure.
If restoration fails, check the router policy before the next experiment.
The bundled Mirai scenario needs `attack-lab:plant-intake:tcp:23` for its
bot-origin probe to reach the Telnet gateway; select the corresponding network
IDs for a custom dataset. Start the capture run with that exception, then
launch `mirai_scenario` separately with the same run ID while capture is active.

## Site-to-site IPsec

`MET-OT-WAN` is the dedicated edge firewall and IKEv2/IPsec hub. The two
remote routers initiate route-based tunnels over the existing untrusted WAN:

| Site | Outer peer addresses: hub / site | VTI: hub / site | Controller key variable |
|---|---|---|---|
| Raw-water | `172.31.255.1` / `172.31.255.11` | `vti10`: `10.20.254.13/30` / `10.20.254.14/30` | `METROPOLIS_IPSEC_RAW_WATER_PSK` |
| Reservoir | `172.31.255.1` / `172.31.255.21` | `vti20`: `10.20.254.17/30` / `10.20.254.18/30` | `METROPOLIS_IPSEC_RESERVOIR_PSK` |

Use two distinct random keys, kept in your password manager. Each must be
32–128 characters using letters, digits, or `_+/=-`. Enter them without
putting their values in commands or shell history:

```bash
read -rsp "Raw-water IPsec key: " METROPOLIS_IPSEC_RAW_WATER_PSK; echo
read -rsp "Reservoir IPsec key: " METROPOLIS_IPSEC_RESERVOIR_PSK; echo
export METROPOLIS_IPSEC_RAW_WATER_PSK METROPOLIS_IPSEC_RESERVOIR_PSK
```

Keep the same keys when reapplying router configuration. The loader validates
them before contacting/configuring routers and substitutes them only into
IPsec PSK commands, without shell evaluation. It redacts keys from errors;
the keys are not saved in run metadata. They do persist in the VyOS appliance
configuration, so protect appliance configurations and console access.

The configured profile uses AES-256-GCM, SHA-256 for IKE, DH/PFS group 19,
1400-byte VTI MTUs, and dead-peer detection. Outer firewall rules admit only
the declared peers on UDP 500/4500 and ESP (IP protocol 50). Decrypted traffic
uses separate VTI zones and the existing protocol/port policy. OSPF/BFD runs
inside the tunnels, with no operational routes or fallback over the clear WAN.
Reconfiguration removes the former Metropolis WAN OSPF/BFD settings; it does
not reconcile arbitrary custom router configuration. The WAN hub's SNMP
target is its internal `10.20.254.10` address. On an existing project, refresh
or recreate `MET-CONTROL-CLIENT-01` so its stored `SNMP_TARGETS` picks up this
change; the builder reports stale settings but does not overwrite them.
The current configs assume the shared, directly connected WAN in the dataset;
an arbitrary routed ISP transport is not modeled.

Apply through `build_topology --configure-routers` or
`experiment_run --configure-routers`. Offline `render_firewall --check`
validates the tunnel declarations and reports peers without needing keys.
Configuration readback does not prove negotiation or packet encryption.
In your manual GNS3 check, inspect `show vpn ike sa`, `show vpn ipsec sa`,
`show ip ospf neighbor`, and `show protocols bfd peer`; confirm encrypted WAN
captures, permitted/blocked application flows, and loss of remote access when
a tunnel is disabled. No live GNS3 verification is included in repository tests.

The command syntax targets [VyOS 1.3 site-to-site IPsec](https://docs.vyos.io/en/1.3/configuration/vpn/site2site_ipsec.html)
and [VTI interfaces](https://docs.vyos.io/en/1.3/configuration/interfaces/vti.html).

## Rebuilds and troubleshooting

| Symptom or change | Check |
|---|---|
| Missing image/template | Build on the correct compute, then register its template |
| Changed node image/environment | The builder reuses nodes by name and does not reconcile these settings; update or recreate affected nodes |
| Missing/colliding link | Inspect `build_topology --plan`, its skipped entries, and capture coverage |
| Switch configuration failure | Docker mapping, OVS kernel support, permissions, and adapter count |
| Empty PCAP | Selected physical link, actual traffic, and capture manifest failures |
| Missing logs | Compute ID/URI, Docker access, node startup, log rotation, source status |
| Missing metrics/charts | Compute Docker access, source status and failures, and the installed development dependencies |
| Legacy static routes | Use the router loader; it removes only routes from earlier Metropolis scripts and its managed `MET_` firewall rules |
| IPsec configuration/negotiation failure | Both controller key variables, matching peer keys, VTI/outer addresses, IKE/ESP policy, and live SA status |
| Need port numbers | `build_topology --plan --write-ports PATH` or the [generated switch reference](../../testbeds/metropolis/switch/README.md) |

Builds reuse existing nodes/links. `--prune-missing` additionally deletes nodes
absent from the plan; use it only when that removal is intended.
