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
| `run.py` | Single entry point for the commands below |
| `_cli.py` | Argument and server plumbing shared by the commands |

## Order of operations

```bash
# 1. Build the images, then register a template for each
python scripts/gns3/run.py register_templates --check
python scripts/gns3/run.py register_templates

# 2. Inspect the plan, then create it
python scripts/gns3/run.py build_topology --plan
python scripts/gns3/run.py build_topology --write-ports derived-ports.yaml

# 3. Start the lab, then load the router configurations
python scripts/gns3/run.py lab_lifecycle --order
python scripts/gns3/run.py lab_lifecycle --start
python scripts/gns3/run.py build_topology --configure-routers

# 4. Inspect physical capture coverage, then capture
python scripts/gns3/run.py capture_traffic --list
python scripts/gns3/run.py capture_traffic --coverage
python scripts/gns3/run.py capture_traffic --all-built --coverage --duration 60
# Or choose individual declared links:
python scripts/gns3/run.py capture_traffic --links link-core-plant attach-intake-plc --duration 60

# 5. Stop it
python scripts/gns3/run.py lab_lifecycle --stop
```

Every command takes `--help`. `build_topology --plan`, `capture_traffic --list`,
and `lab_lifecycle --order` contact no server, so they are safe to run anywhere.

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

Coverage describes modeled paths. Inspect PCAPs containing both local subnet
flows and routed flows before describing the data as broad network coverage.
Uncabled or undeclared paths are outside the capture set.

## Experiment device logs

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

Output is under `datasets/<version>/runs/<run-id>/`: `manifest.json`, an empty
`timeline.jsonl` reserved for actions/status, `logs/devices.jsonl`, and one raw
file per Docker node in `logs/raw/`. Capture runs keep their existing
`datasets/<version>/captures/<capture-run-id>/` location and manifest; the
collector does not mix PCAP records into device logs. The run directory is
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
startup lines unavailable. VyOS routers, switches and other non-Docker nodes
are outside this collector. A remote GNS3 compute requires direct Docker access
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

VyOS and Open vSwitch are appliances, not Docker images, so they cannot be
registered the way the device images are. Import them once in the GNS3 GUI;
`templates.py` names both under `APPLIANCES`, and the builder looks them up by
that name. A blank VyOS appliance also needs its image installed interactively
before it will boot; that is a one-time step per appliance.

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

Start with `--plan`, then `build_topology`, then check the result in the GUI
before starting the lab. A first build is the quickest way to confirm the
appliance names below match your installation.

Two things depend on your installation rather than this code:

- **Appliance template names.** `--router-template` and `--switch-template`
  default to `VyOS 1.3.0` and `Open vSwitch`, which are the usual names. If your
  GNS3 install imported them differently, override them rather than renaming
  anything:

  ```bash
  python scripts/gns3/run.py build_topology --router-template "VyOS 1.4" \
      --switch-template "Ethernet switch"
  ```

- **Compute.** `--compute-id` defaults to `local`. A remote or multi-compute
  server needs the compute that hosts Docker and performs the captures.
