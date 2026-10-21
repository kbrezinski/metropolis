# Understanding this repository, from the ground up

Written for someone who has never seen this project. It assumes you know what a
file and a network are, and nothing else. By the end you should be able to say
what every part of the repository does and what happens when you run it.

---

## 1. What this project is

Imagine a small water-treatment plant. Water comes in from a river, gets
filtered and treated, and is stored in a reservoir that pumps it out to a town.
A plant like that is full of computers: sensors that measure water level,
controllers that open valves, screens that operators watch, and logs that record
what happened.

The computers in these places are not ordinary office computers. They are
**industrial control systems**, and the networks they use are called **OT**
networks, for *operational technology*. (Office networks are called IT,
*information technology*.) OT networks run physical equipment, so a fault or an
attack can spill water, overheat a motor, or stop a town's supply.

This repository builds **a pretend version of that plant inside computers**, so
that security research can be done safely. You can attack the pretend plant,
break it, and study what the attack looked like on the network — none of which
you can do on a real one.

The specific things it provides:

1. **A design for the pretend plant** — which computers exist, what addresses
   they have, and how they are wired together.
2. **Software that pretends to be each device** — a program that behaves like a
   water-level sensor, or a valve controller.
3. **Software that attacks the plant** — so you can generate attack traffic on
   purpose.
4. **Software that builds the plant** in the network simulator, starts it, and
   records the traffic.
5. **Some early scaffolding for the eventual research goal** — detecting these
   attacks automatically.

Two names appear constantly. **Metropolis** is the pretend plant. **Gotham** is
a different, earlier research testbed (an IoT one) whose ideas this project
borrows. When you see "Gotham", read it as "the prior work we are modelling
ourselves on".

---

## 2. The big picture

Here is the whole thing in one diagram.

```
  You, at a terminal
        |
        |  1. build the device images      (Docker)
        |  2. register them in GNS3        (scripts/gns3/)
        |  3. create the topology          (from the YAML files)
        |  4. start the lab                (scripts/gns3/)
        |  5. load router configs          (scripts/gns3/)
        |  6. run an attack                (scripts/attacks/)
        |  7. capture the traffic          (scripts/gns3/)
        v
  +-------------------------------------------------------+
  |  GNS3  - orchestrates: creates the containers,        |
  |          wires them together, starts and stops them   |
  |          (it does not fake anything - see section 10) |
  |                                                       |
  |   +----------------+      +---------------------+     |
  |   |  Routers       |      |  Switches           |     |
  |   |  (move packets |      |  (connect devices   |     |
  |   |   between       |     |   inside one        |     |
  |   |   networks)     |     |   network)          |     |
  |   +----------------+      +---------------------+     |
  |                                                       |
  |   +-----------+  +-----------+  +-------------+       |
  |   | PLC       |  | Sensor    |  | HMI screen  |  ...  |
  |   | (control) |  | (measure) |  | (operator)  |       |
  |   +-----------+  +-----------+  +-------------+       |
  |     each is a real Linux container (Docker) running    |
  |     a real Python program that acts like the device    |
  +-------------------------------------------------------+
        |
        |  real TCP/IP packets cross a real Linux network
        v
  Captured to a .pcap file, which is what you analyse later
```

The three external programs this relies on:

- **Docker** — runs each device in its own isolated Linux system (a *container*).
- **GNS3** — creates those containers, connects them with real virtual cables
  through real routers and switches, and starts and stops them. It also records
  the traffic.
- **Python** — all the software written for this repository is Python (plus a
  little shell script).

---

## 3. The vocabulary you need

Read this section before the file tour; the rest assumes it.

| Term | Plain meaning |
|---|---|
| **Container** | A real, isolated Linux system sharing the host's kernel. Each device is one. |
| **Image** | The recipe a container is created from. Built once, reused many times. |
| **Node** | One device in the simulated network — one container plus its network settings. |
| **Link** | A cable between two nodes. |
| **Subnet** | A range of addresses that can reach each other directly, like `10.20.10.0/24`. |
| **Gateway** | The address of the router a device sends traffic to when the destination is on a different subnet. |
| **VLAN** | A way to keep several logical networks on one physical switch, kept apart. |
| **Trunk** | A cable (or switch port) carrying several VLANs at once. |
| **Protocol** | The agreed language two computers speak. See section 5. |
| **Port number** | A number identifying which service on a computer you want. `502` is Modbus, `1883` is MQTT. |
| **Register** | A numbered slot holding one number, used by industrial protocols. The PLC's memory. |
| **Coil** | Like a register, but on/off instead of a number. Used for switches like "pump on". |
| **PCAP** | A file holding recorded network traffic. What you end up with. |
| **Template** (GNS3) | A reusable definition of a node type, so you can create many similar nodes. |
| **Northbound / Southbound** | Towards the business network / towards the physical equipment. |

---

## 4. Tour of the repository, top to bottom

```
ot-detection-systems/
├── README.md              start here: what it is, how to run it
├── docs/                  this guide lives here
├── schemas/               the rules for the configuration files
├── testbeds/              the pretend plant: its design and its devices
├── scripts/               software that builds, attacks, and checks things
├── src/metropolis/        the beginning of the actual detection software
├── tests/                 automatic checks that the above still works
├── data/                  the *other* (Gotham) dataset, for the old pipeline
├── examples/              a small Jupyter notebook showing basic use
└── pyproject.toml         lists the Python dependencies
```

We will walk each one.

### 4.1 The root files

| File | What it is |
|---|---|
| `README.md` | The front door. Explains the project, its current status in a checkmark table, and the exact commands to run a lab. |
| `pyproject.toml` | Python packaging. Lists dependencies (numpy, pandas, torch, PyYAML…), the dev tools (ruff, pytest, codespell), and pins for the attack toolkit's libraries. |
| `uv.lock` | A precise record of every dependency version, so two machines install identical software. |
| `.python-version` | Says "Python 3.12". |
| `main.py` | A hello-world stub. Ignore it. |
| `AGENTS.md`, `CLAUDE.md` | Instructions for AI coding assistants. They describe the same architecture in denser language — useful as a cross-check but not required reading. |
| `.gitignore` | Tells Git which files not to track: generated captures, `data/`, `.venv`, caches. |
| `.pre-commit-config.yaml` | Runs tidying steps before each commit. |

### 4.2 `docs/` — this guide

Documentation written for a human. Nothing here is executed.

### 4.3 `schemas/` — the rules for the config files

Four JSON files. A **schema** is a written rulebook describing what a valid
configuration file may contain. They exist so a typo is caught by a program
rather than causing a confusing failure later.

| File | Governs | Meaning |
|---|---|---|
| `address-plan.schema.json` | `topology/address-plan.yaml` | What a network allocation looks like: an id, a name, a site, a zone, a CIDR, a gateway, an optional VLAN. |
| `device-inventory.schema.json` | `device_instances/initial_devices.yaml` | What a device entry looks like: name, model, image, site, subnet, address, gateway, protocols, environment variables. |
| `infrastructure.schema.json` | `device_instances/infrastructure.yaml` | What router, switch, and segment entries look like. |
| `link-plan.schema.json` | `topology/links.yaml` | What a cable looks like: an id, a kind, a description, its two endpoints, optional VLANs. |

If a file breaks its schema, `scripts/validate_metropolis_topology.py` says so
and stops. These are not decoration — they are enforced.

### 4.4 `testbeds/` — the pretend plant

Everything describing *what* the plant is. Three parts: the design, the devices,
and the dataset.

#### `testbeds/metropolis/router/` — six router configuration scripts

Routers move traffic between networks. There is one script per router:

| File | The router's job |
|---|---|
| `backbone/router_ot_core.sh` | The hub of the OT side; connects to the plant, the boundary, and the WAN. |
| `backbone/router_ot_dmz.sh` | The boundary router between OT, the DMZ, the enterprise side, and the attack lab. |
| `backbone/router_ot_wan.sh` | Pretends to be the wide-area network connecting remote sites. |
| `locations/router_plant.sh` | The main plant's router. |
| `locations/router_reservoir.sh` | The reservoir/booster station's router. |
| `locations/router_raw_water.sh` | The raw-water lift station's router. |

Each file is a list of **VyOS** commands. VyOS is a real router operating
system; these scripts are real configuration for it. The top of each file
documents, in comments, which numbered network interface connects to what.

#### `testbeds/metropolis/switch/` — the switch reference

Switches connect devices within one network. This directory holds one document,
`README.md`, describing every switch and every port.

It is **generated**, not written by hand:

```bash
uv run python scripts/generate_switch_reference.py --write   # regenerate it
uv run python scripts/generate_switch_reference.py --check   # is it current?
```

It is built from `device_instances/infrastructure.yaml` (the port roles, in
order) and `topology/links.yaml` (which devices connect, and the VLANs), so it
describes the topology the builder actually creates.

**Why generated rather than written.** This directory previously held eight
separate Markdown specifications, one per switch, plus a summary README. They
restated data that was already in the YAML, and no code read them. Eight
documents that drive nothing will eventually describe a network that does not
exist, so they were replaced by one file that cannot drift: if the data changes
and the document does not, a test fails.

Two facts from the old files were worth keeping, and both are now derived:

| Was prose in the specs | Now comes from |
|---|---|
| VLAN mode (trunk or access) | the link's `kind` — `trunk` or `access` |
| Which VLANs a port carries | the link's `vlans` list |

Anything that could not be derived was moved into the data. The one case was a
note that VLAN 10 carries "the PLC, sensor, and legacy Telnet gateway", which now
lives in that link's `description` in `links.yaml`.

Because the roles are listed in order and the port number is the position,
`README.md` shows the exact numbering the build uses — including the ports with
nothing cabled, which it marks *spare*. Those are the extension points: the
reservoir and raw-water sites, for instance, each have a router uplink plus three
empty ports waiting for an RTU and sensors.

Section 4.5 traces one role all the way to a real cable.

#### `testbeds/metropolis/devices/` — the device models

This is the heart of the repository. Each folder is one device type, containing
a `Dockerfile` (how to build its image) and one or more programs that implement
its behaviour.

`devices/README.md` is the reference manual: every image, its environment
variables, and its register map.

| Folder | The device |
|---|---|
| `controllers/plc/` | A **PLC** (programmable logic controller) — the industrial computer that controls equipment. |
| `field/sensors/` | A **sensor** — measures water level and publishes readings. |
| `field/legacy_gateway/` | An old Telnet-accessible device, kept because legacy gear is a real attack target. |
| `field/reachability_probe/` | A deliberately empty host that runs no service, used to test whether routing works. |
| `operations/scada/` | **SCADA** — the supervisory system that polls the PLC and republishes its data. |
| `operations/hmi/` | **HMI** — the screen an operator uses, with buttons and a web page. |
| `operations/historian/` | Records everything it hears for later analysis. |
| `operations/engineering_workstation/` | A real SSH server, like an engineer's laptop. |
| `operations/control_client/` | Pretends to be the routine automation that reads and writes the PLC on a schedule. |
| `services/mqtt_broker/` | The **broker** — the central post office all MQTT messages pass through. |
| `services/dns/` | Turns names into addresses. |
| `services/ntp/` | Supplies the time. |
| `attack/mirai/` | Five nodes pretending to be part of a Mirai botnet. See section 7. |
| `attack/merlin/` | Two nodes pretending to be a Merlin command-and-control setup. |
| `runtime/` | Shared code all devices use: the startup script and the TLS settings. |

#### Which device speaks which protocol

Section 5 explains the protocols themselves. This is the cross-reference, so you
can go from "I see MQTT traffic" to "which device sent it".

**S** = Server: it listens, and others connect to it.
**C** = Client: it connects out to something else.
**P** / **Sub** = MQTT publisher / subscriber.

Names match the inventory file exactly, so you can search for any of them there.

| Device (inventory name) | Modbus | MQTT | CoAP | HTTP | SSH | Telnet | Other |
|---|---|---|---|---|---|---|---|
| `MET-PLC-INTAKE-01` | **S** :502 | | | | | | |
| `MET-RTU-RESERVOIR-01` | **S** :502 | | | | | | |
| `MET-RTU-RAW-WATER-01` | **S** :502 | | | | | | |
| `MET-SENSOR-INTAKE-01` | **C** :502 | **P** :1883 | **S** :5683 | | | | |
| `MET-MQTT-BROKER-01` | | **S** :1883, :8883 | | | | | |
| `MET-SCADA-01` | **C** :502 | **P** :1883 | | | | | |
| `MET-HMI-INTAKE-01` | | **P** + **Sub** :1883 | | **S** :8080 | | | |
| `MET-HISTORIAN-01` | | **Sub** :1883 | | | | | |
| `MET-ENGINEERING-01` | | | | | **S** :22 | | SFTP |
| `MET-CONTROL-CLIENT-01` | **C** :502 | | | | | | DNS, NTP |
| `MET-LEGACY-GATEWAY-01` | | | | | | **S** :23 | |
| `MET-DNS-01` | | | | | | | **S** DNS :53 |
| `MET-NTP-01` | | | | | | | **S** NTP :123 |
| `MET-DEBUG-DMZ-01` | | | | | | | ICMP only |
| `MET-MIRAI-CNC-01` | | | | | | **S** :23 | **S** :48101 |
| `MET-MIRAI-LISTENER-01` | | | | | | | **S** :48101 |
| `MET-MIRAI-LOADER-TFTP-01` | | | | | | | **S** TFTP :69 |
| `MET-MIRAI-LOADER-WGET-01` | | | | **S** :80 | | | |
| `MET-MIRAI-BOT-01` | | | | **C** :80 | | **C** :23 | **C** :48101 |
| `MET-MERLIN-CNC-01` | | | | **S** :8443 | | **S** :23 | |
| `MET-MERLIN-AGENT-01` | | | | **C** :8443 | | | |

Reading a couple of rows out loud:

- **The sensor is the busiest node.** It *reads* the PLC over Modbus (client), it
  *publishes* readings over MQTT, and it *answers* CoAP questions. Three
  protocols, three different roles, one small program.
- **The PLC is a pure server.** It never initiates anything. It sits there
  answering Modbus requests.
- **SCADA sits in the middle.** It is a Modbus client to the PLC and an MQTT
  publisher to the broker. That combination is what makes it a "collector".
- **The control client is a pure client.** Modbus, DNS, and NTP — it only ever
  asks.

**What this means for a capture.** If you capture on the link between the plant
cell and the operations network, you are capturing traffic that must cross a
router, so you see all of it. If you capture on one device's own subnet, you see
only that subnet's local traffic. Section 11 says where to record.

**Three details worth noticing.**

1. The broker listens on **two** MQTT ports: 1883 plaintext and 8883 encrypted.
   Same broker, same content, different transport.
2. The Mirai CNC uses port **23** for its operator console — the same port number
   as Telnet, because that is what real Mirai does. An operator connects to the
   botnet's control server with a Telnet client.
3. The two Mirai loaders differ: the TFTP one serves on port 69, the wget one
   over plain HTTP on port 80. Real Mirai uses both delivery methods, so the lab
   has both, and the resulting traffic differs.

**The shared startup script** — `devices/runtime/network-entrypoint.sh` — runs
before every device program. It reads three environment variables and
configures the container's network accordingly:

- `NODE_HOSTNAME` — the device's name
- `NODE_IP` — its address, e.g. `10.20.10.10/24`
- `NODE_GATEWAY` — where to send traffic for other networks

This is why the same image can become ten different devices: you change the
variables, not the image.

**The shared TLS settings** — `devices/runtime/tls_config.py` — lets any device
speak MQTT over an encrypted connection. See section 6.

#### `testbeds/metropolis/datasets/water_treatment_v1/` — the dataset

A *dataset* is one frozen version of the design. A new version gets a new
folder, so old captures stay explainable.

| Path | Contains |
|---|---|
| `topology/address-plan.yaml` | Every network: its name, purpose, address range, gateway, and VLAN. |
| `topology/links.yaml` | Every cable: 40 of them, each naming its two endpoints. |
| `topology/validation-report.md` | A generated report showing the design checks passed. |
| `device_instances/initial_devices.yaml` | Every device: its name, address, and settings. 21 entries. |
| `device_instances/infrastructure.yaml` | The routers, switches, and access segments. |
| `protocol_profiles/` | *Empty for now* — where per-protocol details will go. |
| `scenarios/` | *Empty for now* — where scripted experiments will go. |
| `metadata/` | *Empty for now* — where run details will go. |
| `captures/` | *Empty for now* — where recorded traffic will go. |

The four YAML files are the single source of truth. Everything else is
generated from them or checked against them.

### 4.5 How a port role becomes a real cable

This is the least obvious step in the whole system, so it is worth following one
cable all the way through. The question it answers: the switch documents talk
about ports by name, so how does a *name* become a physical connection?

**Step 1 — the design names a cable by role.** In `topology/links.yaml`, a cable
between the plant router and its aggregation switch looks like this:

```yaml
- id: link-plant-aggregation
  endpoints:
    - node: MET-PLANT
      interface: eth1          # a Docker node: named by interface
    - node: sw-plant-aggregation
      port_role: router-uplink # a switch: named by role
```

Notice the two endpoints are described *differently*. A router or device names
its network card (`eth1`). A switch names a port **role**, because a switch does
not have card names — it has numbered ports, and which number is which is a
cabling decision.

**Step 2 — the role's position gives the port number.** `topology.py` reads the
switch's `attachment_points` list from `infrastructure.yaml` and turns each role
into the number of its position:

| Position | Role | Becomes port |
|---|---|---|
| 0 | `router-uplink` | 0 |
| 1 | `cell-intake-uplink` | 1 |
| 2 | `cell-dosing-uplink` | 2 |
| … | … | … |

The code that does this is four lines:

```python
mapping[switch["name"]] = {
    role: index for index, role in enumerate(switch["attachment_points"])
}
```

So `router-uplink` means "port 0" purely because it is written first.

**Step 3 — the other endpoint uses its interface number.** `eth1` becomes adapter
1; `eth0` would become adapter 0. Docker nodes have one network card each, so
they always use adapter 0 on the switch side.

**Step 4 — the link is sent to GNS3.** The plan becomes a concrete request naming
two node identities and two port positions:

```json
{"nodes": [
  {"node_id": "<MET-PLANT>",             "adapter_number": 1, "port_number": 0},
  {"node_id": "<sw-plant-aggregation>",  "adapter_number": 0, "port_number": 0}
]}
```

**Step 5 — GNS3 makes it real.** It creates a virtual cable — a Linux `veth` pair —
with one end attached to the switch container's port 0 and the other to the
router container's `eth1`. From that moment there is a genuine network path.

**Why position rather than an explicit number?** Because this is how Open vSwitch
works in GNS3: port numbers are just positions. Writing `router-uplink: 0`
explicitly would duplicate information and give two places to get it wrong. The
cost is that the *order* in the list is load-bearing, which is why the order is
written down in one generated place — `testbeds/metropolis/switch/README.md` —
rather than in eight documents that could each fall out of date.

### 4.6 `scripts/` — the software you run

| Path | What it does |
|---|---|
| `validate_metropolis_topology.py` | Checks the whole design for contradictions. Run this first, always. |
| `gns3/` | Everything that talks to the network simulator. |
| `attacks/` | Everything that generates hostile traffic. |
| `gen_lab_certificates.py` | Creates the lab's own certificate authority, for encryption. |
| `run_gotham_pipeline.py` | The *old* data pipeline, for Gotham's data. Unrelated to Metropolis. |

We cover `gns3/` and `attacks/` in sections 8 and 7.

### 4.7 `src/metropolis/` — the library

This is the installable Python package. It holds the research scaffolding and,
now, the topology library.

| File | What it does |
|---|---|
| `config.py` | Reproducibility settings: fixes the random seeds so two runs give identical results, and picks whether to use a GPU. |
| `paths.py` | Defines the standard folders for data, models, and logs, and creates them on demand. |
| `testbeds.py` | Reads testbed YAML out of the package, so installed users need no repository checkout. |
| `topology/` | **Build, check, write, and draw topologies from Python.** See section 9. |
| `core/__init__.py` | Empty. A placeholder for the detection model. |

There is **no detection model yet**. That is the point of the project and it has
not been built.

### 4.8 `tests/` — the automatic checks

Twenty-one files holding 313 tests. A test is a small program that checks one
behaviour and shouts if it breaks. They run in about 37 seconds and cover:

- the design validates
- the topology library describes the existing dataset exactly
- each device model behaves correctly
- each attack tool works, on loopback
- the simulator client builds the right requests
- encryption actually completes a handshake
- the process model produces sensible numbers

You do not need to read these to understand the system, but if you change
something, run them.

### 4.9 `data/`, `examples/`, `dist/`

- **`data/`** — Gotham's downloaded dataset: thousands of CSV files of recorded
  traffic from Gotham's own testbed. Used only by `run_gotham_pipeline.py`.
  Git-ignored.
- **`examples/test_metropolis_import.ipynb`** — a short notebook demonstrating
  importing the package and loading the address plan.
- **`dist/`** — built Python packages. Generated, not source.

---

## 5. The protocols, explained

A **protocol** is the agreed language two computers speak. These devices speak
six, and which ones matter directly to what traffic you can capture.

Think of each protocol as a different conversation style — some are one-way
announcements, some are question-and-answer, some are direct commands.

### Modbus TCP — the control protocol (port 502)

**What it is.** The oldest and most common industrial protocol. Deliberately
simple: a client asks to read or write numbered memory slots, and the server
answers. It has no authentication, no encryption, and no concept of users. That
is not an oversight; it was designed in 1979 for a serial cable between two
machines in one room.

**Who speaks it here.** The **PLC** is the server. **SCADA** and the **control
client** are clients that poll it. The attack tools can read from and write to
it.

**What it looks like on the wire.** A request naming an address and a count, and
a reply carrying the numbers. Easy to read in a capture — which is why it is the
easiest protocol to attack and to detect attacks against.

**The two kinds of slot:**

- **Holding registers** — hold a number. The PLC here uses four:

  | Number | Holds |
  |---|---|
  | 0 | Water level, as a percentage times ten (so `650` means 65.0%) |
  | 1 | Flow rate, also times ten |
  | 2 | Water quality, also times ten |
  | 3 | A counter that increases every second — a heartbeat, so you can tell the device is alive |

- **Coils** — hold on/off. Coil 0 is "pump running", coil 1 is "valve open".

The "times ten" is a common trick: protocols that only carry whole numbers
multiply decimals to avoid fractions.

### MQTT — the messaging protocol (port 1883, or 8883 encrypted)

**What it is.** A publish-and-subscribe system, like a radio broadcast. Nobody
addresses anybody directly. Instead:

- a **broker** is the central post office
- **publishers** send messages to a *topic* — a name like
  `metropolis/plant/intake/telemetry`
- **subscribers** say which topics they want, and the broker forwards matching
  messages to them

Topics are hierarchical, like folders. A subscriber to `metropolis/#` receives
everything under `metropolis/`.

**Who speaks it here.**

| Node | Role |
|---|---|
| MQTT broker | The post office — everything passes through it |
| Sensor | Publishes water readings |
| SCADA | Publishes PLC snapshots |
| HMI | Publishes operator commands, and displays readings |
| Historian | Subscribes to everything and records it |

**Why it matters here.** Most of your traffic is MQTT. Because it is
publish-subscribe, a single message can fan out to several recipients, which
makes the traffic patterns richer than direct connections.

**Encrypted MQTT (port 8883)** uses **TLS** — the same encryption as HTTPS on a
website. See section 6.

### CoAP — the lightweight protocol (UDP port 5683)

**What it is.** "Constrained Application Protocol" — a cut-down web protocol for
tiny devices that cannot afford full HTTP. It works over UDP, which is faster
and less reliable than TCP: no conversation setup, just fire packets and hope.

It looks a lot like the web: you `GET` a path like `/status`, and you get a
reply with a content type. Unlike the web, it is binary and tiny.

**Who speaks it here.** The **sensor** runs a CoAP server, so the same physical
device can be reached two ways: publish readings over MQTT, and answer direct
CoAP questions.

**What it offers:**

| Path | Returns |
|---|---|
| `/.well-known/core` | A list of everything else available — CoAP's own directory |
| `/status` | All the readings, as JSON |
| `/level`, `/flow`, `/quality`, `/pressure`, `/temperature`, `/turbidity`, `/pump` | One number each, as plain text |

Having many small paths rather than one big one matters for research: a poller
makes a separate round trip per path, so the traffic has visible structure.

**CoAP is not encrypted here.** It has an encrypted form (DTLS), which was
attempted and removed. See the limitation in section 12.

### DNS — names to addresses (port 53)

**What it is.** The phone book. You ask "what is the address of
`plc-intake.metropolis.test`?" and it answers `10.20.10.10`.

**Why it is here.** Real networks have DNS traffic, and the control client uses
it. Without it, the lab's background traffic would look artificial.

### NTP — the time (UDP port 123)

**What it is.** Time synchronisation. Devices ask a server what time it is and
adjust their clocks.

**Why it is here.** Two reasons. First, real networks constantly exchange time
traffic, so it is good background noise. Second, and more important for research:
to compare what happened on two different devices, you need their clocks to
agree. NTP is what makes that possible.

### SSH and Telnet — remote shells (ports 22 and 23)

**What they are.** Ways to log into another computer and type commands.

**SSH** is the modern, encrypted one. The **engineering workstation** runs a
real SSH server, so you can log in as an engineer would.

**Telnet** is the ancient, unencrypted one. The **legacy gateway** runs it
deliberately: old equipment often still exposes Telnet, which is exactly why it
is a common attack target. This node exists to be attacked.

---

## 6. Encryption, explained

**Encryption** scrambles a conversation so that someone watching the network
sees gibberish rather than the contents. Two computers agree on a secret, then
scramble.

For this to work, the computers must trust each other. That trust comes from
**certificates**: electronic documents, signed by someone both sides trust,
saying "this really is the MQTT broker".

In the real world you buy certificates from a public company. Here there is no
internet, and the hostnames only exist inside the simulation, so the lab makes
its **own** authority:

```bash
uv run python scripts/gen_lab_certificates.py --out-dir certs
```

That produces three files:

| File | What it is |
|---|---|
| `ca.crt` | The certificate of the lab's own authority. Everyone trusts this. |
| `server.crt` | The broker's certificate, signed by that authority. |
| `server.key` | The broker's private key — the secret that proves it is the broker. |

Then:

- the **broker** is told `MQTT_TLS=true` and where the files are, and starts an
  encrypted listener on port **8883** alongside the plaintext one on 1883
- each **client** is told `TLS=true` and where `ca.crt` is

The four MQTT clients — sensor, SCADA, HMI, historian — all read the same
settings, from one shared file (`devices/runtime/tls_config.py`). The attack
toolkit can also connect encrypted, with `--tls --tls-ca certs/ca.crt`.

**Why bother?** Because if you can only ever capture plaintext, you can only
ever test detection against plaintext. Encrypted traffic is harder: you cannot
read the contents, so you must reason from *patterns* — who talked to whom, how
often, how much. That is a different and more interesting detection problem.

---

## 7. The attack software, explained

This repository contains working attack tools. That may look alarming. Two
things make it reasonable:

1. They are pointed at a **simulated** plant, on a **private** address range,
   and they refuse public addresses.
2. The default Python payloads are **simulated**. They do not download or
   execute malware. A user-supplied Gotham `.sh` file selected with
   `--gotham-script` runs on the invoking host and must be reviewed separately.
   Section 7.3 explains exactly how far the bundled Python tools go.

`scripts/attacks/` holds two kinds of thing. **Tools** are individual attacks you
run one at a time. **Drivers** replay a whole attack life cycle. All are launched
through one entry point, `run.py`. Their files are grouped by attack family:

```bash
python scripts/attacks/run.py --help
python scripts/attacks/run.py network_recon_scan --dry-run
```

The bundled Python attacks resolve targets from the selected dataset inventory.
Use `--dataset` or `METROPOLIS_DATASET` for a user-defined topology, and `--node`
when several nodes offer the same service. An explicit private `--host` and
`--port` can target a service outside the end-device inventory. Live attacks
require `METROPOLIS_LAB_ACK=yes`, a deliberate
speed bump so nothing fires by accident. The optional Gotham shell path follows
the same gate, but its own target selection and traffic limits come from that
shell script.

### 7.1 The individual tools

| Tool | The attack it performs |
|---|---|
| `network_recon_scan.py` | Looks for open services across the lab. The reconnaissance an attacker does first. |
| `iot_discovery.py` | A lighter version of the same, probing a fixed list of ports. |
| `mqtt_bruteforce_attack.py` | Guesses MQTT passwords from a short list. |
| `mqtt_flood_attack.py` | Sends many MQTT messages; can also plant or clear a *retained* message (one the broker keeps and delivers to future subscribers). |
| `ssh_bruteforce_attack.py` | Guesses SSH passwords, then runs one harmless command to prove access. |
| `mirai_infection_sim.py` | Guesses Telnet passwords, then runs one harmless command. |
| `coap_response_measurement.py` | Measures bounded CoAP response sizes and optional private-lab reflection traffic. |
| `modbus_manipulation_attack.py` | Writes to the PLC's registers and coils — changing the water level the plant believes it has. |
| `hping3_attack.py` | Generates bounded packet traffic for availability experiments. Its supported profiles and safety limits are documented with the attack toolkit. |

### 7.2 The two drivers

These replay an entire attack life cycle rather than one step.

**`run_mirai_choreography.py`** walks through a Mirai botnet infection, in
order: each bot checks in with the command server, reports what it found, the
operator lists its bots, sends a command, and the bot fetches a payload. Every
step uses the real protocol, so the resulting traffic has the *shape* of a
botnet.

**`run_merlin.py`** does the same for a command-and-control tool: start a
listener, an agent checks in, the operator lists and selects it, sends a command,
and the agent uploads a file.

### 7.3 What is simulated, and why

Gotham, the prior work, uses real malware binaries. This repository does not, and
cannot. So the attack lab is built from **Simulated versions** that produce the
same *traffic* without the same *danger*.

| What Gotham does | What happens here |
|---|---|
| Downloads and runs a real Mirai bot binary | A small program that opens connections and sends messages, and does nothing else |
| Runs a real Merlin agent binary | A program that accepts a fixed list of harmless commands and refuses everything else |
| Uploads a real DDoS tool | A loader that serves a text file |
| Runs delivered commands | Commands are *recorded*, never run |

Two guarantees, both enforced in code and covered by tests:

- **Nothing executable is ever transferred.** The "payload" is the literal text
  `metropolis-synthetic-payload-stub`.
- **Nothing arbitrary is ever executed.** The agent accepts only a handful of
  named, harmless operations.

A third, structural: the attack lab has its own subnet. The router scripts
already include routes to and from the plant, while the planned inter-zone
firewall policy is not applied. Verify actual reachability in GNS3 before
interpreting an attack capture.

The honest consequence: this traffic is botnet-*shaped*, not real malware. It is
good enough for a detector to learn patterns from. It is not evidence about how
real Mirai behaves.

---

## 8. The automation, explained

`scripts/gns3/` is the software that builds and runs the lab. It is split into
purpose, plus a couple of shared helpers.

### 8.1 Shared plumbing

| File | What it does |
|---|---|
| `_transport.py` | The lowest level: sends HTTP requests to GNS3 and reads replies. The only file that knows about the network protocol used to control GNS3. |
| `client.py` | A friendly wrapper: `create_node`, `create_link`, `start_capture`, and so on. One method per thing you can ask GNS3 to do. |
| `config.py` | Finds where GNS3 is running and how to log in, by reading its settings file or environment variables. |
| `_cli.py` | Small helpers shared by the commands: loading the YAML files, connecting, printing errors consistently. |
| `device_logs.py` | Follows Docker stdout/stderr and writes normalized device events plus raw logs. |
| `experiment.py` | Coordinates a run's PCAP capture, device logs, router syslog, timeline, and run manifest. |

### 8.2 The data-to-topology layer

| File | What it does |
|---|---|
| `topology.py` | Reads the four YAML files and works out the complete plan: which nodes to create, where to draw them on the canvas, and — importantly — **which port number on each switch each cable uses**. Those numbers do not exist in the design, because they only exist once something is built. This file derives them. |
| `templates.py` | Lists the GNS3 template each device image needs. |

### 8.3 The four commands

| Command | What it does |
|---|---|
| `register_templates.py` | Tells GNS3 about each Docker image, once, so nodes can be created from them. Safe to rerun. |
| `build_topology.py` | Creates the project, all 48 nodes, all 40 cables, and optionally loads each router's configuration. |
| `lab_lifecycle.py` | Starts and stops the lab in a sensible order, and reports status. |
| `capture_traffic.py` | Checks capture coverage and records packets on chosen links for a chosen duration, then downloads and verifies the PCAPs. |
| `collect_device_logs.py` | Collects Docker device logs and router syslog for a run. |
| `experiment_run.py` | Runs a coordinated experiment, giving captures and logs one run ID and recording a timeline and manifest. |

They are launched through a single dispatcher, `run.py`, exactly like the attack
tools:

```bash
python scripts/gns3/run.py build_topology --plan
```

`--plan` on any of these means "show me what you would do and change nothing".

### 8.4 Two pieces worth understanding

**Startup order.** Devices cannot be started at random. If SCADA starts before
the PLC, it fails to connect and may give up. So `lab_lifecycle.py` sorts the
nodes into tiers and starts them in order:

1. switches, 2. routers, 3. the PLC and the broker, 4. publishers like the
sensor, 5. polling clients like SCADA.

Stopping happens in reverse, so consumers outlive producers.

**Router configuration.** A router that has been created but not configured does
nothing: it has no addresses and no idea where to send anything. So
`build_topology.py --configure-routers` logs into each router's console, types in
the commands from its script, and then **reads back** the router's own view of
its interfaces to confirm the configuration took. If an address is missing, it
tells you.

### 8.5 What the builder has to decide

One design decision is worth knowing about, because it explains a file's shape.

The switch specifications list ports by *role* (`router-uplink`, `rtu-or-plc`)
and leave the real numbers as a "TODO", because the numbers depend on how someone
cables it. Since software does the cabling here, the software is the only thing
that can know the numbers. So `topology.py` assigns them in the order the
specification lists the roles, and the mapping can be written out with
`--write-ports` for a human to copy back into the documents.

### 8.6 How an experiment records what happened

Use `experiment_run` when you want packet captures and device-side evidence to
share one run ID. It can start the lab, configure the routers, collect logs, and
capture selected links (or all built physical links). The collector starts before
lab startup so it can include device startup logs.

```bash
python scripts/gns3/run.py experiment_run --help
python scripts/gns3/run.py experiment_run --start-lab --configure-routers \
  --duration 60 --docker-host local=ssh://gns3@gns3-vm
```

The run manifest and event timeline are saved under
`testbeds/metropolis/datasets/water_treatment_v1/runs/<run-id>/`. Normalized
Docker events go to `logs/devices.jsonl`; router messages forwarded to the NTP
node's syslog receiver go to `logs/syslog.jsonl`; original Docker output is kept
per node under `logs/raw/`. PCAP files and a capture manifest go under
`captures/<run-id>/`. The manifest links the artifacts, records hashes, packet
counts, times, coverage and failures, and identifies the topology and dataset
version used for the run. These generated run files are ignored by Git.

`capture_traffic` can still be used by itself. Its coverage report compares the
declared links with the physical GNS3 links and warns about links without a
capture point. Capturing router uplinks does not see traffic switched entirely
inside a local subnet. The combined runner records unavailable links and
collection failures; it does not claim complete network visibility.

Docker logs are read from each compute that hosts Docker nodes, using the
`--docker-host COMPUTE_ID=URI` mapping. The collector normalizes timestamps and
structured events, and redacts common credential patterns. Review raw logs
before sharing them because arbitrary application text can still contain
secrets. VyOS routers forward syslog over UDP to the NTP service node; UDP can
drop messages, and Open vSwitch logs are not collected by this path. Run schemas
are in `schemas/experiment-run.schema.json` and
`schemas/device-log-event.schema.json`.

---

## 9. Building topologies from Python

Everything in section 4.4 is data: four YAML documents describing the plant. That
data is the authority, and it works well, but writing YAML by hand is a poor way
to explore alternatives. `metropolis.topology` is the same thing as a Python
library, so a topology can be built, changed, checked, and drawn in code.

The two are not alternatives. The library reads and writes exactly those YAML
documents, so you can build in Python and still use every existing script.

### 9.1 The whole idea in one example

```python
from metropolis.topology import Device, Interface, Network, Router, Switch, Topology

lab = Topology(testbed_id="demo", description="Two-cell plant")

# Subnets
lab.add_network("cell-a", "10.0.10.0/24", name="Cell A", vlan_id=10, gateway="10.0.10.1")
lab.add_network("cell-b", "10.0.11.0/24", name="Cell B", vlan_id=11, gateway="10.0.11.1")

# A router with two ports, and a switch whose ports are named by role
lab.add(
    Router("R-EDGE", config="router_edge.sh", interfaces=(
        Interface("eth0", cidr="10.0.254.1/30"),
        Interface("eth1", kind="trunk", vlans=(10, 11)),
    )),
    Switch("sw-a", attachment_points=("router-uplink", "plc-uplink")),
)

# A host
lab.add_device("PLC-A", image="metropolis/controller:dev",
               subnet="10.0.10.0/24", address="10.0.10.10/24", gateway="10.0.10.1")

# The cables, and what each carries
lab.link("link-r-sw", "R-EDGE:eth1", "sw-a:router-uplink",
         kind="trunk", vlans=(10, 11), description="Edge trunk")
lab.link("link-sw-plc", "sw-a:plc-uplink", "PLC-A:eth0", kind="access")

lab.validate()          # raises, listing every contradiction
print(lab.summary())    # what it contains
print(lab.to_mermaid())  # draw it
lab.save("testbeds/metropolis/datasets/demo_v1")   # write the four documents
```

Reading a link endpoint is the part worth noticing: `"R-EDGE:eth1"` means a
router's interface, and `"sw-a:router-uplink"` means a switch's port role. The
suffix beginning with `eth` is an interface; anything else is a port role. You
can also pass a mapping if you prefer to be explicit.

### 9.2 What it can do

| Method | What it does |
|---|---|
| `add(...)`, `add_network`, `add_device`, `add_switch`, `add_segment` | Add nodes |
| `link(id, a, b, vlans=, network_id=, ...)` | Connect two endpoints |
| `add_device(name, subnet="cell-a", auto_network=True)` | Add a host, resolving the subnet and allocating an address |
| `allocate_address(subnet)` | The next free host address |
| `ports_of(switch)`, `port_vlans(switch, role)`, `port_mode(switch, role)` | What a switch port carries |
| `problems()` | Every contradiction, as readable text |
| `validate()` | Raise if there are any |
| `to_dicts()` / `to_yaml()` | The four documents, as data or as text |
| `save(dir)` / `from_directory(dir)` / `from_bundled()` | Write and read datasets |
| `to_dot()` / `to_mermaid()` / `draw.render()` | Draw it — text, or an image |
| `summary()` | A short text description |
| `switch(name).port_number(role)` | Where a role lands on a switch |

### 9.3 Letting it fill in the obvious parts

Writing out every address and repeating each `NODE_*` value is tedious and easy
to get wrong. Three conveniences remove that:

```python
lab.add_network("cell-a", "10.0.10.0/24", vlan_id=10, gateway="10.0.10.1")

# The subnet can be named by network id. Without an address, the next free one
# is allocated, skipping the gateway and anything already used.
lab.add_device("PLC-A", image="...", subnet="cell-a", address="10.0.10.10/24")

# auto_network derives the gateway and the three NODE_* values the container
# entrypoint applies, so identity is stated once and cannot drift.
lab.add_device("SENSOR-A", image="...", subnet="cell-a", auto_network=True)

# Name the network on a link and the VLAN comes from it: one VLAN is an access
# port, several is a trunk, and the kind follows automatically.
lab.link("link-sw-sensor", "sw-cells:ops-uplink", "SENSOR-A:eth0",
         network_id="cell-a")
```

`auto_network` is off by default. That matters: with it off, nothing is invented,
which is why the checked-in dataset round-trips unchanged.

A switch port's VLANs are **not** written twice. The port table names roles in
order — that is what decides port numbers — and what each port carries comes from
the link plugged into it:

```python
Switch.with_ports("sw-cells", (
    SwitchPort("router-uplink", mode="trunk"),   # mode may be declared
    SwitchPort("cell-a-uplink", mode="access"),
))

lab.port_vlans("sw-cells", "cell-a-uplink")   # -> (10,), from the link
lab.port_mode("sw-cells", "router-uplink")    # -> "trunk"
lab.ports_of("sw-cells")[1].vlans            # attribute access
lab.ports_of("sw-cells")[1]["vlans"]         # or mapping access
```

If a port does state its VLANs and they disagree with its link, `problems()`
reports it rather than silently picking one.

### 9.4 The checks it runs

`problems()` looks for the mistakes that are easy to make and hard to see:

- the same name used twice
- two subnets that overlap
- a gateway outside its own subnet
- a device whose address is not in its subnet
- a device on a subnet that is not in the address plan
- a device whose `NODE_IP` or `NODE_GATEWAY` disagrees with its recorded address
  (these are what the container entrypoint applies, so a mismatch means the
  device would come up on the wrong address)
- a router with no interfaces, or an interface that is neither an address nor a
  trunk
- a link to a node that does not exist
- a link naming a switch port role that switch does not have
- a link pointing at a subnet that is not in the address plan
- a link with an unparsable address

None of this replaces `scripts/validate_metropolis_topology.py`, which also
cross-checks the router scripts and the schemas. It catches things earlier, in
the code where you made the change.

### 9.5 Drawing it

Three ways, from simplest to richest.

**`to_mermaid()`** produces a diagram that renders in Markdown:

```
graph TD
  subgraph plant_intake["Plant intake process cell<br/>10.20.10.0/24"]
    MET_PLC_INTAKE_01["MET-PLC-INTAKE-01"]
  end
  MET_OT_CORE["MET-OT-CORE"]
  sw_plant_aggregation{{"sw-plant-aggregation"}}
  MET_OT_CORE -->|trunk vlan 10,11| sw_plant_aggregation
```

**`to_dot()`** produces Graphviz source, for feeding to `dot` yourself.

**`draw.render()`** produces an actual image, using **networkx and matplotlib**:

```bash
uv run python -m metropolis.topology draw --out topology.png \
    --title "Metropolis water-treatment testbed"
```

The topology becomes a networkx graph, `networkx.spring_layout` places the
nodes, and networkx's own matplotlib drawing functions render it. There is no
hand-written layout code: the positions are the library's.

What the picture tells you:

- **Nodes are coloured by type**, with a legend giving the counts.
- **Every endpoint shows its name and its address**, so the addressing reads
  straight off it.
- **Links are labelled with kind and VLANs.** Trunks are solid and purple;
  everything else is dashed. Red is deliberately avoided, because in an OT
  diagram red reads as a fault.

`--seed` fixes the layout, because a force-directed layout is random unless it
is told not to be: the same seed always gives the same picture. `--dpi` raises
the resolution, and `--out` accepts `.png`, `.svg` or `.pdf` — the vector
formats stay sharp at any size.

matplotlib and networkx are not runtime dependencies. They are imported only
inside `draw.render()`, so installing the package pulls in neither.
`draw.available()` reports whether they can be used, and the command line
explains how to install them if not.

**What a force layout does not give you.** It groups by connectivity, not by
tier, so the picture does not read as a stack of zones, and a node with no links
has no forces acting on it and is pushed away from the rest. For a diagram with
a clear top-to-bottom order, `to_dot()` hands the graph to Graphviz, whose
`dot` layout is built for exactly that.

### 9.6 The command line

```bash
uv run python -m metropolis.topology show                # what is in it
uv run python -m metropolis.topology check               # contradictions?
uv run python -m metropolis.topology draw --out topology.png
uv run python -m metropolis.topology render --format mermaid
uv run python -m metropolis.topology render --format dot --out topology.dot
uv run python -m metropolis.topology export --out /tmp/regenerated
```

With no `--dataset`, the copy bundled inside the package is used, so these work
from an installed wheel with no repository checkout.

### 9.7 A worked example

`examples/build_a_topology.py` builds a small two-cell plant, checks it, and can
draw and write it:

```bash
uv run python examples/build_a_topology.py --draw
uv run python examples/build_a_topology.py --write /tmp/demo_v1
uv run python examples/build_a_topology.py --break-it    # see the checks fire
```

### 9.8 How this relates to the existing dataset

The library was proved against the real data rather than the other way round:
loading `water_treatment_v1` and writing it straight back produces all four
documents **identically**, field for field. That is a test, so it cannot quietly
stop being true.

Two consequences worth knowing:

- The library is a complete model of the current topology, not an approximation.
- Existing tools are unaffected. The GNS3 builder still reads the YAML, the
  validator still reads the YAML, and the switch reference is now rendered
  through the library rather than re-parsing YAML itself.

One limitation: comments in hand-written YAML are not preserved. Reading a
dataset and writing it again keeps all the data but loses the `#` notes. In
practice the dataset documents are generated rather than hand-edited, so this
rarely matters.

---

## 10. What is actually running

This is the most commonly misunderstood part, so it gets its own section. The
short answer: **when the lab is running, there is no simulation of the devices
happening.** They are real Linux systems running real programs.

Three questions, answered in order: is it a program? what is in the Linux
environment? what is GNS3 doing?

### 9.1 GNS3 is the only "program" — and it is a manager, not a simulator

GNS3 is ordinary software. You install it like any other application, and it runs
as a background service on a Linux machine (or inside a virtual machine). It has
a web interface and a desktop client.

What it *is not* is a program that pretends to be a network. It does not invent
fake packets, and it does not simulate an operating system. It is a **manager**:
you tell it "here are 48 nodes, here are 40 cables", and its job is to create
those things and wire them up using facilities the real operating system already
has.

That distinction is the whole answer. GNS3's role is *orchestration* —
create, connect, start, stop. Everything after that is genuinely happening.

### 9.2 Each device is a real container running a real program

Every device is a **Docker container**. A container is not a simulation of a
computer; it is a real, isolated Linux system sharing the host's kernel. It has
its own:

- **process table** — its own list of running programs
- **filesystem** — its own files, isolated from the host and from other containers
- **network stack** — its own interfaces, its own IP address, its own routing table

So `MET-PLC-INTAKE-01` is not a *description* of a PLC. It is:

| | |
|---|---|
| Base image | `python:3.12-slim` — a real Debian Linux |
| Running process | one Python program, `modbus_controller.py`, as process number 1 |
| What it is doing | listening on TCP port 502, waiting for Modbus requests |
| Its identity | whatever IP address the entrypoint script gave it |

That Python program is an ordinary program. It is not a mock or a stub. It opens
a TCP socket with normal socket calls, the same ones any network program uses.
When SCADA asks it to read register 0, the program looks up a number in a Python
dictionary and sends it back. From inside the container there is nothing fake
about any of it — it believes it is a device listening on a network.

The base images used:

| Base | Used by | Why |
|---|---|---|
| `python:3.12-slim` | Most devices — PLC, sensor, SCADA, HMI, historian, workstation, control client, and the attack nodes | Debian with Python; these devices are Python programs |
| `alpine:3.21` | The legacy gateway and the reachability probe | Small, and neither needs Python here |
| `debian:bookworm-slim` | DNS and NTP | Plain Debian; these services need no Python |
| `eclipse-mosquitto:2` | The MQTT broker | The real Mosquitto broker, not an imitation |
| VyOS 1.3.0 | The routers | A real router operating system |
| Open vSwitch | The switches | Real switching software |

Note the third row. The MQTT broker is **real Mosquitto** — the same software
that runs in production systems. The routers are **real VyOS**, running real
routing. Only the *devices* are purpose-written, because a real PLC costs money
and a simulated one does not.

### 9.3 What happens inside a container, step by step

Follow `MET-SENSOR-INTAKE-01` from nothing to running. This is the same sequence
every device follows.

**Step 1 — GNS3 creates the container.** It uses the image, and applies the node's
configuration: which GNS3 project it belongs to, which switch ports its network
interfaces attach to.

**Step 2 — Linux starts the entrypoint.** The image's `ENTRYPOINT` is
`/usr/local/bin/metropolis-entrypoint`, which is our script
`devices/runtime/network-entrypoint.sh`. It runs *before* the device program and
its job is networking:

```sh
# what it actually does, in order
hostname "$NODE_HOSTNAME"

# wait for the network card to appear, up to 30 seconds
until ip link show dev "$NODE_INTERFACE"; do sleep 1; done

ip link set dev "$NODE_INTERFACE" up
ip -4 address replace "$NODE_IP" dev "$NODE_INTERFACE"
ip -4 route replace default via "$NODE_GATEWAY" dev "$NODE_INTERFACE"

exec "$@"          # now hand over to the device program
```

If `NODE_IP` is `10.20.10.10/24`: the card is brought up, given that address, and
the default route is pointed at `10.20.10.1`. These are the same commands you
would type on any Linux machine. Nothing is simulated — this container now
genuinely has that address.

Two details worth knowing. The **interface wait** exists because GNS3 may
connect the virtual cable a moment after the container starts; without the wait,
configuration would fail intermittently. And it **verifies preconditions**: if
you ask for a gateway but no address, or the image lacks `iproute2`, it stops
with a clear message rather than starting a device that cannot reach anything.

**Step 3 — it starts the device.** `CMD` is `python /opt/metropolis/mqtt_sensor.py`.
Python starts, imports its libraries, reads environment variables for its
settings, and binds its sockets:

- a UDP socket on **5683** for CoAP, so others can query it
- an outbound connection to the broker on **1883**, to publish readings
- an outbound connection to the PLC on **502**, to read the process value

**Step 4 — it does its job, forever.** A loop wakes every five seconds, reads the
PLC's register, builds a JSON document, publishes it, and sleeps. If the PLC is
unreachable it logs a warning and keeps its last known value.

**Step 5 — packets genuinely travel.** When the sensor publishes, the data leaves
as a real TCP segment, crosses a real Linux bridge, arrives at the broker
container, and is delivered to whatever subscribed. If a router sits between
them, one of the VyOS containers forwards it, applying its real routing table.

**Step 6 — stopping sends a signal.** The container is killed, Linux tears down
the process, and the address disappears. Nothing to clean up.

### 9.4 So is the traffic real?

**Yes — on a real (if private) network.** The packets are genuine TCP/IP packets,
captured with genuine packet capture, readable in Wireshark exactly as traffic
from a real plant would be. The protocols are the real protocols. Mosquitto is
real Mosquitto. VyOS is real VyOS.

What is *pretend* is narrower than people assume:

| Genuinely real | Pretend |
|---|---|
| The Linux systems | The sensor's *measurement* — it is a formula, not water |
| The network, addresses, routing, switching | The plant itself — no pipes, no water |
| The packets; the capture | The topology's physical layout |
| The protocols; the broker; the routers | The device models' behaviour |
| The attack tools' network behaviour | The malware payloads |

That is why this is a useful research testbed. A detector trained on this traffic
is seeing the real statistical properties of real protocol conversations — just
not the consequences of them.

### 9.5 What runs where

A useful mental map of the whole thing:

| Layer | What it is | Real or written here |
|---|---|---|
| Container runtime | Docker | Real, third-party |
| Network orchestration | GNS3 | Real, third-party |
| Routers | VyOS containers | Real OS, our config |
| Switches | Open vSwitch | Real, third-party |
| MQTT broker | Eclipse Mosquitto | Real, third-party |
| DNS / NTP | Our small services | Written here |
| PLC, RTU, sensor, SCADA, HMI, historian, workstation, control client, legacy gateway | Our Python programs | Written here |
| Mirai / Merlin nodes | Our Python programs | Written here |
| Attack tools | Our Python programs | Written here |

The purpose-written parts are the two bottom-ish rows — the *devices*. Everything
they run on top of is real software, which is what makes their traffic worth
studying.

---

## 11. What actually happens when you run it

Follow one full session.

```bash
# 0. Check the design for contradictions. Changes nothing.
uv run python scripts/validate_metropolis_topology.py

# 1. Build the device images. Each becomes a reusable template.
docker build -f testbeds/metropolis/devices/controllers/plc/Dockerfile -t metropolis/controller:dev .
#    ...and 18 more; the devices README lists them all.

# 2. Tell GNS3 about those images.
python scripts/gns3/run.py register_templates --check     # does every device have one?
python scripts/gns3/run.py register_templates             # create them

# 3. See what would be built, then build it.
python scripts/gns3/run.py build_topology --plan
python scripts/gns3/run.py build_topology

# 4. Start everything in the right order, then configure the routers.
python scripts/gns3/run.py lab_lifecycle --start
python scripts/gns3/run.py build_topology --configure-routers

# 5. Watch it. Normal traffic begins immediately: the sensor publishes
#    readings, SCADA polls the PLC, the historian records it all.
python scripts/gns3/run.py lab_lifecycle --status

# 6. Attack it.
export METROPOLIS_LAB_ACK=yes
python scripts/attacks/run.py network_recon_scan --dry-run   # what would happen
python scripts/attacks/run.py network_recon_scan             # do it

# 7. Record an experiment. The same run ID ties PCAPs, device logs,
#    router syslog, timeline events, and the manifest together.
python scripts/gns3/run.py experiment_run --duration 60 \
  --docker-host local=ssh://gns3@gns3-vm

# Or capture selected links by itself:
python scripts/gns3/run.py capture_traffic --list
python scripts/gns3/run.py capture_traffic --links link-core-plant --duration 60

# 8. Stop.
python scripts/gns3/run.py lab_lifecycle --stop
```

**What is happening underneath, in order:**

1. The validator reads four YAML files and cross-checks them.
2. Docker builds 19 images.
3. GNS3 learns about those images as templates.
4. The builder reads the YAML, resolves 48 nodes and 40 cables, assigns switch
   port numbers, creates the project, the nodes, and the cables, and sets each
   node's network settings.
5. Starting brings containers up tier by tier.
6. Router configuration pushes six configuration files into six routers and
   verifies the result.
7. Normal traffic flows: MQTT messages through the broker, Modbus polls to the
   PLC, DNS lookups, NTP exchanges, CoAP answers.
8. The attack tool adds hostile traffic, and can write a JSONL event log.
9. The experiment collector follows Docker logs, receives forwarded router
   syslog, and captures packets on selected physical links.
10. It writes verified PCAPs, normalized and raw logs, a timeline, and manifests
    that connect the artifacts and report gaps or failures.

**What you end up with:** PCAPs you can inspect in Wireshark, device and router
logs, and a manifest/timeline for the run. The attack tool's own JSONL events are
separate evidence; they describe the tool's actions, while PCAP and device logs
show observed network and device behavior. Together these sources help correlate
what was attempted with what the testbed recorded.

---

## 12. Honest limits

Read this before drawing conclusions from anything above.

| Limit | Why it matters |
|---|---|
| **Use only an isolated lab GNS3 server** | The attack tools are designed for the Metropolis lab. Confirm the selected GNS3 project and network before running traffic-generating commands. |
| **Live collection depends on the lab setup** | The collector needs Docker access to each relevant GNS3 compute, router syslog forwarding, and physical capture points. Its manifests report missing sources and links; check them before interpreting a run. |
| **Coverage is not automatic or complete** | Local switched traffic is invisible from a router uplink, and uncabled paths have no capture point. Use the coverage report and inspect the resulting PCAPs. |
| **No firewall or encryption on the network path** | Routers move traffic but do not restrict it. Attacks can reach anything routable. |
| **CoAP is plaintext** | Its encrypted form (DTLS) was attempted, did not work, and was removed. Plaintext CoAP is complete. |
| **Only three device families** | Gotham, the prior work, has twelve. Fewer distinct behaviours means less for a detector to tell apart. |
| **The process model is light** | The PLC's values are coupled and move realistically, but this is not a real hydraulic model. A register write is not a model of real damage. |
| **No automatic labelling or detection model** | Run artifacts are collected and correlated, but the repository does not yet automatically label captures or provide a detection model. |
| **The attack lab is simulated** | Its traffic is botnet-shaped, not real malware. |

---

## 13. Where to look for what

| You want to know… | Read |
|---|---|
| What this project is, and how to run it | `README.md` |
| What a device can do and how to configure it | `testbeds/metropolis/devices/README.md` |
| What addresses and services exist | `testbeds/metropolis/datasets/water_treatment_v1/` |
| Which attack to run, and against what | `scripts/attacks/README.md`, then `SERVICE_TARGETS.md` |
| How to build and run the lab | `scripts/gns3/README.md` |
| How experiment PCAPs, device logs, and syslog are collected | `scripts/gns3/README.md`, especially “Experiment device logs” |
| What a dataset run stores | `testbeds/metropolis/datasets/water_treatment_v1/README.md` |
| What is finished and what is not | the checkmark table in `README.md` |
| The deep technical detail | `AGENTS.md` / `CLAUDE.md` |
| Whether a change broke something | run `uv run pytest` |
