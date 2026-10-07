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

# 4. Capture what you need
python scripts/gns3/run.py capture_traffic --list
python scripts/gns3/run.py capture_traffic --links link-core-plant --duration 60

# 5. Stop it
python scripts/gns3/run.py lab_lifecycle --stop
```

Every command takes `--help`. `build_topology --plan`, `capture_traffic --list`,
and `lab_lifecycle --order` contact no server, so they are safe to run anywhere.

## What the builder does with the switch ports

The switch specifications list port *roles* and leave the GNS3 port numbers as a
`TODO`, because they are only known once a project is cabled. The builder is what
makes them real: each role gets the adapter matching its position in the
specification's own list, and `--write-ports` emits the mapping so the specs can
be filled in from something that ran.

`links.yaml` deliberately does not assert port numbers, and neither does this
code invent them — it derives them and writes them back.

## What is repeated and what is skipped

Building is idempotent: a node or link that already exists is reused, so a build
that stopped partway resumes instead of duplicating. `--prune-missing` deletes
project nodes the plan does not contain.

Two things are reported rather than created:

- **A link endpoint naming an access segment** with no port role — those
  segments are documented as segments, not appliances, so no port number exists.
- **A node whose template is not registered** — run `register_templates` first.

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

## Not verified against a live server

The API paths and payloads were checked against the published GNS3 API
reference, and every module is unit-tested with a fake client. No run against a
live GNS3 server has been performed, so treat the first build as the test: start
with `--plan`, then `build_topology`, then check the result in the GUI before
starting the lab.

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
