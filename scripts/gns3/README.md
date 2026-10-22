# GNS3 automation

Code that talks to a GNS3 controller, so the Metropolis topology does not have
to be assembled by hand in the GUI.

The topology stays declarative, in
`testbeds/metropolis/datasets/water_treatment_v1/`, and is checked by
`scripts/validate_metropolis_topology.py`. Everything here reads that data and
makes the server match it.

| File | Purpose |
|---|---|
| `_transport.py` | HTTP and authentication against the GNS3 v2 API |
| `client.py` | One method per API operation the other modules need |
| `config.py` | Finds the local server settings, or takes them from the environment |
| `templates.py` | The Docker template each image needs |
| `topology.py` | Reads the declared topology; works out coordinates and ports |
| `builder.py` | Creates the project, nodes, and links |
| `lifecycle.py` | Startup and shutdown order, and waiting for readiness |
| `capture.py` | Capture control on chosen links |
| `device_logs.py` | Read-only Docker stdout/stderr collection and normalization |
| `firewall_policy.py` | Resolve permitted flows and render VyOS 1.3 zone policies |
| `experiment.py` | One run ID and manifest for PCAP, device logs, and router syslog |
| `run.py` | Single entry point for the commands below |
| `_cli.py` | Argument and server plumbing shared by the commands |

## Order of operations

```bash
# 1. Build the images, then register a template for each
# Include metropolis/actuator:dev for intake, dosing, and filtration I/O;
# metropolis/analyzer:dev for dosing; and metropolis/dp-instrument:dev for filtration.
python scripts/gns3/run.py register_templates --check
python scripts/gns3/run.py register_templates

# 2. Inspect the plan, then create it
python scripts/gns3/run.py build_topology --plan
python scripts/gns3/run.py build_topology --write-ports derived-ports.yaml

# 3. Start the lab, then load the router configurations
python scripts/gns3/run.py render_firewall --check  # offline policy and route check
python scripts/gns3/run.py lab_lifecycle --order
python scripts/gns3/run.py lab_lifecycle --start
python scripts/gns3/run.py build_topology --configure-routers

# 4. Inspect physical capture coverage, then capture
python scripts/gns3/run.py capture_traffic --list
python scripts/gns3/run.py capture_traffic --coverage
python scripts/gns3/run.py capture_traffic --all-built --coverage --duration 60
# Or choose individual declared links:
python scripts/gns3/run.py capture_traffic --links link-core-plant attach-intake-plc --duration 60
# Dosing: local analyzer polling, cyclic I/O, and routed SCADA polling:
python scripts/gns3/run.py capture_traffic --links attach-dosing-analyzer attach-dosing-duty-drive attach-dosing-standby-drive link-plant-cell-dosing-uplink --duration 60
# Filtration: local pressure polling, cyclic backwash I/O, and routed SCADA polling:
python scripts/gns3/run.py capture_traffic --links attach-filtration-dp attach-filtration-backwash-pump attach-filtration-backwash-valve link-plant-cell-filtration-uplink --duration 60

# For a complete experiment, collect logs and PCAPs together:
python scripts/gns3/run.py experiment_run --start-lab --configure-routers --duration 60 \
  --docker-host local=ssh://gns3@gns3-vm

# 5. Stop it
python scripts/gns3/run.py lab_lifecycle --stop
```

Every command takes `--help`. `build_topology --plan`, `capture_traffic --list`,
`lab_lifecycle --order`, and `render_firewall` contact no server.

## Routed firewall policy

`topology/firewall-policy.yaml` lists baseline flows by source and destination
LAN, protocol, and destination port. Each source or destination accepts an
address-plan network ID (such as `scada` or `plant-intake`), a `vlan-N` alias,
or a CIDR contained in one declared LAN. A list of selectors expands to each
source/destination combination. Rules match the full selected CIDRs, so hosts
added to those networks inherit the permission. `router/NAME` is a special
source for syslog emitted by a router's local zone; it uses that router's
egress address. `render_firewall --check` resolves selectors and checks the
forward and return paths over declared OSPF peers; without
`--check`, it prints the flow report and generated VyOS commands as JSON. The
policy is validated against `schemas/firewall-policy.schema.json`.
`build_topology --configure-routers` and `experiment_run --configure-routers`
apply the generated rules after the router interface and OSPF commands. The
loader removes exact legacy Metropolis static routes and its own `MET_` zones and rule sets before reapplying them;
unrelated VyOS firewall configuration is left alone. It checks that the managed
names appear in the router's reported configuration. Actual packet enforcement
still requires the user's offline GNS3 validation.

Every routed interface has its own default-drop zone, including a local zone
for traffic to and from each router. Permitted new device flows match source and
destination CIDRs, protocol, and port; established and related return traffic is
allowed. Undeclared DMZ, enterprise, and attack-lab flows are denied. A router
firewall cannot filter two devices sharing one VLAN, such as the intake PLC and
its drives. OSPF supplies return routes for SCADA and syslog traffic.

The six router scripts use OSPFv2 area 0 for inter-router routing.
Core-to-plant, core-to-DMZ, and core-to-WAN `/30` links use OSPF
point-to-point mode. The shared WAN underlay is a broadcast OSPF segment for
the WAN hub and both remote routers. OT LANs are advertised passively, so no
OSPF hellos are sent to device ports. Enterprise and attack-test networks are
also advertised passively to enable return routing. The generated local
zone policy permits IP protocol 89 only on declared OSPF peer interfaces and
from their named peer addresses. Adjacency changes are sent to router syslog.
The firewall policy still controls cross-zone traffic. The offline compiler
rejects missing or ambiguous OSPF paths instead of assuming that static routes
will carry traffic.

These are checked-in settings, not a claim of live adjacency. In GNS3, use
`show ip ospf neighbor`, `show ip ospf route`, and `show ip route` on the VyOS consoles and
capture a transit or WAN-underlay link to confirm hello and update packets.
`render_firewall --check` validates the declared OSPF peers and their cables
without contacting GNS3.

For an existing project, run `build_topology --configure-routers` to migrate
all six routers. The loader reads each running configuration, removes only the
exact static routes installed by the earlier Metropolis scripts, commits the
OSPF configuration, and reports any legacy route still present on readback.
Check each router's route table for OSPF learned remote prefixes and the absence
of those old static routes. Running a `.sh` router script manually does not
remove routes already saved on an appliance; use the loader for this migration.

For a controlled attack experiment, add `--attack-allow
SOURCE-NET:DEST-NET:tcp|udp|icmp:PORT` to `experiment_run`, together with
`--configure-routers`. Use `-` as the port for ICMP. The source CIDR must be
within `attack-lab`; the destination must be a different declared LAN. Use a
network ID, VLAN alias, or contained CIDR to choose each side. Repeat the
option for more than one protocol/port combination. For example:

```bash
python scripts/gns3/run.py experiment_run --configure-routers --duration 60 \
  --attack-allow attack-lab:plant-intake:tcp:502 \
  --docker-host local=ssh://gns3@gns3-vm
```

The compiler rejects selectors outside the declared LANs, LANs without a
declared link, and paths without the required cabled OSPF neighbors. An
exception can use a narrower contained CIDR, such as `10.99.10.0/25`, when
the whole attack network is too broad. These are network permissions; local
traffic within one VLAN does not pass through a router firewall.

The exception is recorded in the run manifest and timeline. The runner
reapplies the baseline in `finally`, including after capture errors or Ctrl-C,
and reports a failed restoration. Confirm the router policy state before the
next run if restoration failed. Apply attack traffic separately while the run
captures; `experiment_run` does not launch an attack script.

## Capture coverage and files

`--coverage` compares each `links.yaml` ID with GNS3's actual node, adapter and
port numbers. It reports built, missing, ambiguous and miswired links. `--all-built`
selects every physical link that is neither suspended nor already capturing,
including undeclared GNS3 links labeled `gns3_<link-id>` in the report. A
declared segment or attachment without a matching physical link is not a capture
point. The report lists host attachment links needed for local subnet traffic,
router and trunk links needed for routed traffic, and devices or segments with
no physical cabling. Selecting a router uplink alone cannot capture traffic
switched entirely within a local subnet.

Each run retrieves PCAPs from the GNS3 controller into the versioned dataset's
`captures/<run-id>/` directory. A file is accepted only if it contains at least
one complete packet. The adjacent `manifest.json` records declared and GNS3
link IDs, UTC start and stop times, topology and dataset versions, server and
local paths, packet counts, SHA-256 hashes, coverage and failures. An empty or
missing capture is a failed run. Captures are git-ignored; preserve the manifest
with the dataset when publishing or archiving a run.
The original file remains at the server-side `capture_file_path` returned by
GNS3, which can be on a remote compute. The manifest records that exact path;
the verified dataset copy is `captures/<run-id>/<declared-link-id>.pcap` (or
`gns3_<link-id>.pcap` for an undeclared physical link). The coverage report and
manifest list unavailable declared links and never assert full network coverage.

Coverage describes modeled paths. Inspect PCAPs containing both local subnet
flows and routed flows before describing the data as broad network coverage.
Uncabled or undeclared paths are outside the capture set.

For the dosing cell, local Modbus TCP analyzer polls should appear on
`attach-dosing-analyzer` or `attach-dosing-plc`; cyclic EtherNet/IP UDP 2222
should appear on each drive attachment. SCADA-to-PLC Modbus crosses
`link-plant-cell-dosing-uplink`. The dosing HMI's MQTT traffic crosses
`link-plant-hmi-dosing-uplink` and its own `attach-hmi-dosing` link. Check
`--coverage` first, then confirm packets and timing in the copied PCAPs. The
new cell's live GNS3 behavior is not established by the offline plan checks.

For filtration, the PLC polls the differential-pressure instrument over local
Modbus TCP every second. The backwash pump and valve use TCP 44818 setup and
cyclic UDP 2222 at a nominal 100 ms interval. SCADA polls the filtration PLC
over routed Modbus TCP every 5 seconds, and the filtration HMI's MQTT command
and acknowledgement traffic crosses `link-plant-hmi-filtration-uplink`.
Capture `attach-filtration-dp`, both backwash actuator attachments,
`link-plant-cell-filtration-uplink`, and `attach-hmi-filtration` or its uplink
to check these expectations. The 25–80 kPa, 120-second independent pressure
waveform should cross the default 65 kPa threshold, with an 8-second backwash
request and a 10 kPa rearm band. The waveform does not model backwash changing
physical filter pressure. Confirm packet timing and startup in the user's GNS3
environment before claiming a verified run.

## Experiment device logs

`experiment_run` is the combined command. It starts Docker log collection,
optionally starts the lab with `--start-lab`, then captures all built physical
links for `--duration` seconds. Add `--configure-routers` to push the six VyOS
scripts after startup; this enables their syslog forwarding for a fresh lab.
The console password defaults to the lab's VyOS default and can be set through
`METROPOLIS_ROUTER_PASSWORD` without putting it in a command line.
`--links ID ...` limits capture points. All files
share one run ID: the run manifest, timeline, `logs/devices.jsonl`, and
`logs/syslog.jsonl` are under `runs/<run-id>/`; PCAPs and their detailed manifest
are under `captures/<run-id>/`. The run manifest includes the capture manifest,
link IDs, PCAP paths, hashes, timestamps, unavailable declarations and failures.
`--start-lab` leaves nodes running after the experiment; stop them with
`lab_lifecycle --stop` when appropriate.

Six VyOS 1.3 router scripts forward `info` and higher syslog to the NTP service
node at `10.20.30.11:514/UDP`. The NTP image must be rebuilt after this change.
The receiver emits structured lines to its Docker stdout; the collector writes
router-attributed copies to `logs/syslog.jsonl` and reports each router that
sent no messages. UDP delivery is not guaranteed. The Metropolis Open vSwitch
image follows its `ovsdb-server.log` and `ovs-vswitchd.log` files and emits
structured operational records into Docker stdout. GNS3 Docker switches have
no routed management interface, so they cannot send UDP directly to the NTP
receiver. The run collector bridges those marked records into the same
`logs/syslog.jsonl` file, attributes them to the GNS3 switch node, and records
`docker_stdout_bridge` as their transport. Ordinary switch stdout and stderr
remain container records. Missing switch events and daemon log files are
reported as failures; the image also emits a database health event every 15
seconds. The manifest lists every expected router and switch.

Start the collector **before** starting the lab, then run the existing
`lab_lifecycle --start` command in another terminal. It polls for Docker nodes
and uses Docker's `--since` option to include startup messages from the run's
start time even when a container appears after collection begins.

```bash
python scripts/gns3/run.py collect_device_logs \
  --run-id 20261020T031500Z_baseline_001 --duration 600 \
  --docker-host local=ssh://gns3@gns3-vm
# In another terminal, while collection is running:
python scripts/gns3/run.py lab_lifecycle --start
```

Supply one `--docker-host COMPUTE_ID=URI` for each GNS3 compute that hosts
Docker nodes. The compute ID comes from the project's node records, not the
controller host. The Docker CLI must be installed on the collector machine and
able to use the supplied SSH connection, Unix socket or Windows named pipe.
Use SSH keys or an agent for remote computes; password-bearing Docker host URIs
and unauthenticated TCP Docker endpoints are rejected. This reads Docker logs
and does not execute commands inside containers or change device state.

Output is under `datasets/<version>/runs/<run-id>/`: `manifest.json`,
`timeline.jsonl` for run actions and project status, `logs/devices.jsonl`,
`logs/syslog.jsonl`, and one raw file per Docker node in `logs/raw/`. A standalone
`collect_device_logs` run leaves the timeline empty; `experiment_run` fills it.
PCAPs remain in `datasets/<version>/captures/<run-id>/`; their packet records
never enter device logs. The run directory is
ignored by Git. The new schemas are `schemas/experiment-run.schema.json` and
`schemas/device-log-event.schema.json`.

Every normalized line has a collector UTC timestamp. Docker's event timestamp
is stored separately as `device_timestamp`; structured JSON fields remain in
`attributes`. Plain text, malformed JSON, and continuation lines are preserved
as individual records. Common password/token assignments and PEM private-key
blocks are redacted in both normalized and raw output. Review raw logs before
sharing them because arbitrary application text may still contain secrets.

The manifest is written at start and updated on source/failure changes. A
successful run requires at least one line from every attached Docker source;
unstarted nodes, missing compute mappings, Docker errors and empty output are
reported as failures. Docker log-driver retention/rotation can make older
startup lines unavailable. VyOS routers reach the syslog relay through the lab
network; switch daemon events use the Docker log path from their compute. The
syslog manifest records source kinds, transport, counts, and missing sources.
Non-Docker nodes remain outside the container collector.
A remote GNS3 compute requires direct Docker access
on that compute via the configured SSH connection; the controller API does not
offer a Docker stdout/stderr relay.

## What the builder does with the switch ports

The infrastructure inventory lists switch port *roles*, and the port number is
the role's position in that list. `links.yaml` deliberately does not assert port
numbers and this code does not invent them — it derives them from the order.

`testbeds/metropolis/switch/README.md` shows the resulting numbering for every
switch, generated from the same data, so it always matches what a build does. If
you want the mapping as data rather than as a document, `--write-ports` emits it
as YAML.

Switches now use the repo-owned `metropolis/openvswitch:dev` Docker image. Build
that image on every GNS3 compute that will host switches, then register the
`Metropolis Open vSwitch` template. The template has 16 NICs; `ethN` is the
adapter assigned to role number N. The builder starts each switch, sends its
declared port settings to the container through Docker, and checks OVS's actual
port state. It sends nothing for spare ports and removes undeclared ports from
the managed bridge. A rerun skips unchanged OVS settings. The command reports
each failure and exits with status 1 if any switch remains unverified.

```bash
docker build -f testbeds/metropolis/devices/infrastructure/openvswitch/Dockerfile -t metropolis/openvswitch:dev .
python scripts/gns3/run.py register_templates
python scripts/gns3/run.py build_topology --docker-host local=unix:///var/run/docker.sock
```

For a remote compute, use `--docker-host COMPUTE_ID=ssh://user@compute-host`.
Docker CLI access to that compute is required. The switch image starts OVS
itself and needs a compute with Open vSwitch kernel support and the network
permissions GNS3 gives Docker nodes. A missing module, container, Docker
connection, or port is reported as a configuration failure.

## What is repeated and what is skipped

Building is idempotent: a node or link that already exists is reused, so a build
that stopped partway resumes instead of duplicating. `--prune-missing` deletes
project nodes the plan does not contain.

One thing is reported rather than created:

- **A node whose template is not registered** — run `register_templates` first.

Access segments get distinct GNS3 adapter numbers for each declared attachment,
in `links.yaml` order. The builder checks full endpoint ports before reusing a
link and reports conflicting wiring instead of treating any link between the
same two nodes as equivalent.

## Router and switch appliances

VyOS remains a GUI-imported appliance. The switch is a repo-owned Docker image
and is registered with the other Docker templates. A blank VyOS appliance needs
its image installed interactively before it will boot.

## Loading the router configurations

The six VyOS scripts in `testbeds/metropolis/router/` are the source of truth
for addressing and routing. Start the routers, then load each script onto its
node:

```bash
python scripts/gns3/run.py lab_lifecycle --start
python scripts/gns3/run.py build_topology --configure-routers
```

This logs into each router's console, enters configuration mode, sends the
script's commands one at a time, and then reads the router's own view of its
interfaces. Any address the script configures but the router does not report is
printed, so a configuration that did not take is visible instead of assumed.

The VyOS appliance's default account is `vyos`/`vyos`, which is what Gotham logs
in with. Override it if you changed it:

```bash
python scripts/gns3/run.py build_topology --configure-routers \
    --router-username admin --router-password secret
```

Gotham does the same job by uploading the script and checking an MD5 checksum,
driving the console with fixed prompt strings and sleeps. This uses the same
approach without the sleeps: it waits for each prompt, and it confirms the
result from the running configuration rather than the upload.

## Pointing at a server

The server is read from the GNS3 client's own settings, newest version first, so
an upgrade does not hide it:

```text
~/.config/GNS3/<version>/gns3_server.conf        # Linux
%APPDATA%\GNS3\<version>\gns3_server.conf        # Windows
```

Environment variables override the file, which is what you want for a remote or
containerised server:

| Variable | Meaning |
|---|---|
| `GNS3_SERVER_HOST` | Controller host |
| `GNS3_SERVER_PORT` | Controller port |
| `GNS3_SERVER_USERNAME` | Basic auth user, if the server requires one |
| `GNS3_SERVER_PASSWORD` | Basic auth password |

```bash
GNS3_SERVER_HOST=10.0.0.5 GNS3_SERVER_PORT=3080 python scripts/gns3/run.py build_topology
```

With a host set and no config file present, the port defaults to 3080. With
neither, the run stops with an explanation rather than guessing an address.

## First run

Start with `--plan`, build the Docker images and register templates, then run
`build_topology` with the Docker host mapping. Check the result in the GUI
before starting the lab.

Two things depend on your installation rather than this code:

- **Router template name.** `--router-template` defaults to `VyOS 1.3.0`.
  Override it if your imported VyOS appliance has another name. The switch
  template is registered from this repository and should keep its default name:

  ```bash
  python scripts/gns3/run.py build_topology --router-template "VyOS 1.4" \
      --docker-host local=unix:///var/run/docker.sock
  ```

- **Compute.** `--compute-id` defaults to `local`. A remote or multi-compute
  server needs the compute that hosts Docker and performs the captures.
