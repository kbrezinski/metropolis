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

Clone the repository onto every compute that will host its images. Run this
block from the repository root **on that compute**:

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
docker build -f "$DEVICES/operations/hmi/Dockerfile" -t metropolis/hmi:dev .
docker build -f "$DEVICES/operations/historian/Dockerfile" -t metropolis/historian:dev .
docker build -f "$DEVICES/operations/engineering_workstation/Dockerfile" -t metropolis/engineering:dev .
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
build command. The router scripts target VyOS 1.3; changing a template name
does not make them compatible with another VyOS release.

## 4. Start and configure

```bash
uv run python scripts/gns3/run.py lab_lifecycle --start
uv run python scripts/gns3/run.py build_topology --configure-routers \
  --docker-host "$COMPUTE_DOCKER"
```

Startup orders infrastructure, field devices, controllers, services, and polling
clients. The router loader applies interface, OSPF, BFD, SNMP, syslog, and
generated firewall settings, then checks the reported configuration.

The router console defaults are `vyos` / `vyos`. If changed, use the builder's
`--router-username` and `--router-password` options. For `experiment_run`,
the password override is the `METROPOLIS_ROUTER_PASSWORD` environment variable.

Check cabling, VLAN isolation, router neighbors, permitted/blocked paths, and
service startup in your GNS3 environment. Offline checks validate declarations.

## 5. Collect an experiment

On a running lab, choose a **new run ID**:

```bash
uv run python scripts/gns3/run.py experiment_run \
  --run-id baseline_001 --duration 120 --docker-host "$COMPUTE_DOCKER"
```

Defaults: capture every built, capturable physical link for 60 seconds, collect
container diagnostics and syslog, and generate a run ID if omitted.

| Option | Effect |
|---|---|
| `--links ID ...` | Capture only these declared links |
| `--start-lab` | Begin log collection before starting nodes |
| `--configure-routers` | Apply router settings after startup |
| `--dataset PATH` | Read a different dataset for the run |
| `--project NAME` | Use a different GNS3 project |

To collect startup logs, use `--start-lab --configure-routers` on a stopped lab.
Nodes remain running when collection finishes. Launch any
[attack scenario](../attacks/README.md#record-an-attack-with-a-capture) separately
with the same run ID, then stop the lab when finished:

```bash
uv run python scripts/gns3/run.py lab_lifecycle --stop
```

Topology building and lifecycle currently load the bundled dataset. Lifecycle
accepts `--dataset` but does not apply it yet. Registration can select
`--inventory`; capture, log, experiment, and firewall commands apply `--dataset`.

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

Capture host attachments for local subnet exchanges and uplinks for routed
traffic. A router uplink does not see traffic switched within one VLAN.
Selection alone does not prove complete traffic coverage.

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
| `runs/<run-id>/attacks.jsonl` | Actions from separately launched attack commands |
| `runs/<run-id>/logs/devices.jsonl` | Normalized container diagnostics |
| `runs/<run-id>/logs/syslog.jsonl` | Router syslog and bridged switch events |
| `runs/<run-id>/logs/raw/` | Original container lines, with secret redaction |

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

## Routed firewall policy

The dataset's `topology/firewall-policy.yaml` permits flows by source network,
destination network, protocol, and port. Selectors accept address-plan network
IDs, `vlan-N` aliases, or CIDRs contained in a declared LAN. Permissions cover
all hosts in those networks. Router-originated syslog uses `router/NAME`.

The generated policy drops other new routed traffic, allows established
replies, and includes router-local zones. Same-VLAN traffic bypasses it.
Router scripts declare OSPF area 0 with passive device LANs, BFD on peer links,
and read-only SNMP polling from the control client.

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

## Rebuilds and troubleshooting

| Symptom or change | Check |
|---|---|
| Missing image/template | Build on the correct compute, then register its template |
| Changed node image/environment | The builder reuses nodes by name and does not reconcile these settings; update or recreate affected nodes |
| Missing/colliding link | Inspect `build_topology --plan`, its skipped entries, and capture coverage |
| Switch configuration failure | Docker mapping, OVS kernel support, permissions, and adapter count |
| Empty PCAP | Selected physical link, actual traffic, and capture manifest failures |
| Missing logs | Compute ID/URI, Docker access, node startup, log rotation, source status |
| Legacy static routes | Use the router loader; it removes only routes from earlier Metropolis scripts and its managed `MET_` firewall rules |
| Need port numbers | `build_topology --plan --write-ports PATH` or the [generated switch reference](../../testbeds/metropolis/switch/README.md) |

Builds reuse existing nodes/links. `--prune-missing` additionally deletes nodes
absent from the plan; use it only when that removal is intended.
