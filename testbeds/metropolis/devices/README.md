# Metropolis emulated devices

Docker images that simulate the devices in the Metropolis water-treatment lab:
PLCs, sensors, an HMI, a broker, and a set of synthetic attack nodes.

Each image is one reusable device model. Create as many GNS3 nodes from an image
as you need, and give each node its own identity and address through environment
variables.

These are protocol simulators, not vendor firmware. They speak Modbus, MQTT,
CoAP, HTTP, SSH, Telnet, DNS, and NTP well enough to generate realistic traffic
and to be attacked. The process values they hold are not connected by a
hydraulic model, so nothing a device reports has a physical cause.

## Quick start

Build the image, create a GNS3 Docker template from it, set the node's
environment, and start it:

```bash
docker build -f testbeds/metropolis/devices/controllers/plc/Dockerfile -t metropolis/controller:dev .
```

```text
NODE_HOSTNAME=MET-PLC-INTAKE-01
NODE_IP=10.20.10.10/24
NODE_GATEWAY=10.20.10.1
NODE_INTERFACE=eth0
```

Every image sets its own address this way before starting its application, so
you do not configure addresses inside GNS3 beyond these variables. Nodes do not
publish Docker ports — they bind to their own GNS3 interfaces.

## The image set

| Device | Source | What it does | Suggested network |
|---|---|---|---|
| PLC / RTU | `controllers/plc/` | Modbus TCP server with a small holding-register and coil map; one image serves either role | Plant VLANs 10-13, or remote site LANs 14-15 |
| MQTT and CoAP sensor | `field/sensors/` | Publishes MQTT telemetry; answers CoAP discovery and status on UDP 5683 | Plant or remote-site LAN |
| MQTT broker | `services/mqtt_broker/` | Authenticated Mosquitto broker for telemetry and HMI commands | VLAN 23, `10.20.23.0/24` |
| SCADA collector | `operations/scada/` | Polls a Modbus controller and republishes the snapshot over MQTT | VLAN 20, `10.20.20.0/24` |
| HMI panel | `operations/hmi/` | Authenticated HTTP panel, MQTT subscription, and bounded setpoint publisher | HMI VLANs 50-53 |
| Historian | `operations/historian/` | Subscribes to MQTT and logs received events to its console | VLAN 22, `10.20.22.0/24` |
| Engineering workstation | `operations/engineering_workstation/` | Real OpenSSH shell and SFTP, with Python Modbus and MQTT libraries | VLAN 21, `10.20.21.0/24` |
| Routine control client | `operations/control_client/` | Resolves the PLC by DNS, samples NTP, and periodically reads and writes Modbus | VLAN 21, `10.20.21.0/24` |
| Legacy gateway | `field/legacy_gateway/` | Telnet login and shell, for experiments that need legacy host access | Intake VLAN 10 |
| DNS | `services/dns/` | Local records for this dataset; no upstream forwarding | OT services VLAN 30 |
| NTP | `services/ntp/` | Local time replies, using the host clock without changing it | OT services VLAN 30 |
| Reachability probe | `field/reachability_probe/` | A minimal host that runs no service, so each zone has something to ping or route to | Any zone that needs a positive control |
| Mirai chain | `attack/mirai/` | Command console, scan listener, loaders, and an inert bot agent | Attack lab `10.99.10.0/24` |
| Merlin C2 | `attack/merlin/` | C2 console and an inert agent | Attack lab `10.99.10.0/24` |

Actuators are represented by writable Modbus coils on the PLC and RTU — pump run
and valve open — rather than separate containers. A central log collector
remains future work.

## Build the images

Run these from the repository root. The build context is the root, so the
Dockerfiles can copy the shared model code.

```bash
docker build -f testbeds/metropolis/devices/controllers/plc/Dockerfile -t metropolis/controller:dev .
docker build -f testbeds/metropolis/devices/field/sensors/Dockerfile -t metropolis/mqtt-sensor:dev .
docker build -f testbeds/metropolis/devices/services/mqtt_broker/Dockerfile -t metropolis/mqtt-broker:dev .
docker build -f testbeds/metropolis/devices/operations/scada/Dockerfile -t metropolis/scada:dev .
docker build -f testbeds/metropolis/devices/operations/hmi/Dockerfile -t metropolis/hmi:dev .
docker build -f testbeds/metropolis/devices/operations/historian/Dockerfile -t metropolis/historian:dev .
docker build -f testbeds/metropolis/devices/operations/engineering_workstation/Dockerfile -t metropolis/engineering:dev .
docker build -f testbeds/metropolis/devices/operations/control_client/Dockerfile -t metropolis/control-client:dev .
docker build -f testbeds/metropolis/devices/field/legacy_gateway/Dockerfile -t metropolis/legacy-gateway:dev .
docker build -f testbeds/metropolis/devices/services/dns/Dockerfile -t metropolis/dns:dev .
docker build -f testbeds/metropolis/devices/services/ntp/Dockerfile -t metropolis/ntp:dev .
docker build -f testbeds/metropolis/devices/field/reachability_probe/Dockerfile -t metropolis/reachability-probe:dev .
docker build -f testbeds/metropolis/devices/attack/mirai/cnc/Dockerfile -t metropolis/mirai-cnc:dev .
docker build -f testbeds/metropolis/devices/attack/mirai/scan_listener/Dockerfile -t metropolis/mirai-scan-listener:dev .
docker build -f testbeds/metropolis/devices/attack/mirai/loader/Dockerfile -t metropolis/mirai-loader:dev .
docker build -f testbeds/metropolis/devices/attack/mirai/wget_loader/Dockerfile -t metropolis/mirai-wget-loader:dev .
docker build -f testbeds/metropolis/devices/attack/mirai/bot/Dockerfile -t metropolis/mirai-bot:dev .
docker build -f testbeds/metropolis/devices/attack/merlin/cnc/Dockerfile -t metropolis/merlin-cnc:dev .
docker build -f testbeds/metropolis/devices/attack/merlin/agent/Dockerfile -t metropolis/merlin-agent:dev .
```

Building creates local images only. It does not create GNS3 templates or place
nodes in a project.

## How a node configures itself

Every image uses `devices/runtime/network-entrypoint.sh` as its entrypoint.
Before starting the application it:

1. Sets the hostname from `NODE_HOSTNAME`, if given.
2. Waits up to 30 seconds for `NODE_INTERFACE` (default `eth0`) to appear, if
   `NODE_IP` is set.
3. Brings the interface up and applies `NODE_IP`.
4. Sets the default route through `NODE_GATEWAY`, if given.

Copy the values from
[`initial_devices.yaml`](../datasets/water_treatment_v1/device_instances/initial_devices.yaml)
into each GNS3 node. GNS3 does not read that file. The values needed are:

```text
NODE_HOSTNAME=MET-PLC-INTAKE-01
NODE_IP=10.20.10.10/24
NODE_GATEWAY=10.20.10.1
NODE_INTERFACE=eth0
```

A node's interface must be connected to a GNS3 link before it starts, or the
entrypoint finds nothing to configure. Each model supports one configured IPv4
interface and one default gateway; for a node with several NICs, point
`NODE_INTERFACE` at the one on the intended subnet.

Leaving `NODE_IP` empty skips address setup. Setting `NODE_GATEWAY` without
`NODE_IP` is an error. To run these images as ordinary Docker services with
custom addresses rather than as GNS3 nodes, the container needs the `NET_ADMIN`
and `SYS_ADMIN` capabilities.

## Per-image environment variables

Beyond the shared `NODE_*` values above:

| Image | Variables |
|---|---|
| `metropolis/controller:dev` | `DEVICE_ROLE=PLC`, `MODBUS_HOST=0.0.0.0`, `MODBUS_PORT=502`, `PROCESS_LEVEL=650`, `PROCESS_FLOW=120`, `PROCESS_QUALITY=950` |
| `metropolis/mqtt-sensor:dev` | `SENSOR_TYPE`, `SENSOR_VALUE`, `PLC_HOST`, `SENSOR_REGISTER`, `SENSOR_DIVISOR`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_HOST`, `MQTT_PORT=1883`, `MQTT_TOPIC`, `PUBLISH_MODE=persistent\|per-message`, `COAP_ENABLED=true`, `COAP_PORT=5683` |
| `metropolis/mqtt-broker:dev` | Builds its password file at startup. Synthetic accounts: `lab_device` / `LabOnly_Device_2026` and `lab_operator` / `LabOnly_MQTT_2026`. Listens on TCP 1883. |
| `metropolis/scada:dev` | `PLC_HOST`, `MODBUS_PORT=502`, `MODBUS_UNIT_ID=1`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_HOST`, `MQTT_PORT`, `MQTT_TOPIC`, `POLL_INTERVAL=5` |
| `metropolis/hmi:dev` | Required `HMI_PASSWORD`; `HMI_USERNAME=operator`, `HMI_HTTP_HOST=0.0.0.0`, `HMI_HTTP_PORT=8080`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_HOST`, `MQTT_PORT`, `MQTT_TOPIC`, `MQTT_TELEMETRY_TOPIC`, `PUBLISH_INTERVAL=30`, `SETPOINT=65.0` |
| `metropolis/historian:dev` | `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_HOST`, `MQTT_PORT`, `MQTT_TOPIC=metropolis/#` |
| `metropolis/engineering:dev` | Required `ENGINEER_PASSWORD`; OpenSSH on TCP 22 as the fixed non-root account `engineer` |
| `metropolis/control-client:dev` | `PLC_DNS_NAME=plc-intake.metropolis.test`, `DNS_SERVER`, `NTP_SERVER`, `MODBUS_PORT`, `MODBUS_UNIT_ID`, `CONTROL_INTERVAL=30` |
| `metropolis/legacy-gateway:dev` | Required `LEGACY_ROOT_PASSWORD`; BusyBox Telnet on TCP 23 with a lab-only root login |
| `metropolis/dns:dev` | Local records on UDP/TCP 53; edit `services/dns/dnsmasq.conf` to change this dataset's names |
| `metropolis/ntp:dev` | Local NTP on UDP 123 |

Use the fixed addresses from the topology plan. The addresses shown in this
document are examples for the first lab instance; record your final assignments
in `device_instances/` before you collect traffic.

## HMI and engineering workstation

| Node | Endpoint | Lab username / password |
|---|---|---|
| `MET-HMI-INTAKE-01` | `http://10.20.50.10:8080/` | `operator` / `LabOnly_HMI_2026` |
| `MET-ENGINEERING-01` | SSH and SFTP at `10.20.21.10:22` | `engineer` / `LabOnly_Engineering_2026` |

Attach the engineering node to VLAN 21 and the HMI to VLAN 50 using the existing
switch specifications. If an engineering template carries a start-command
override such as `sleep infinity`, clear it so the image's SSH startup runs.

### HMI

The panel shows the latest SCADA payload and its timestamp, and accepts numeric
level setpoints from 0 to 100 percent. Setpoints are published to the MQTT
command topic periodically and after each operator change. **Nothing consumes
those commands**, so a setpoint does not reach the PLC.

All routes require HTTP Basic authentication: `/`, `/api/status`, `/healthz`,
and `POST /api/setpoint`. A setpoint request is a JSON body of `{"value":65}`
with `Content-Type: application/json` and an `X-Metropolis-Request: hmi`
header. A `202` means the request was accepted locally — not that the broker
delivered it or the PLC acknowledged it. `last_publish_queued_at` records local
queue acceptance only. `/healthz` reports HTTP availability and the MQTT
connection flag separately, so telemetry can be stale while MQTT is connected.
State is in memory and resets on restart.

The panel is a small Python HTTP server. HTTP and its credentials are
unencrypted, and the passwords in this document are synthetic test fixtures.

### Engineering workstation

This is a real OpenSSH server, not a restricted command emulator: a non-root
shell with SFTP. Root login and forwarding are disabled, and the account has no
sudo. Host keys are generated on first start in `/var/lib/metropolis/ssh`;
persist that directory per node if host identity should survive container
replacement, and never share a host-key volume between nodes. Passwords remain
visible in the node's configuration.

To check normal use from a routed host:

```bash
curl -u operator http://10.20.50.10:8080/api/status
ssh engineer@10.20.21.10
sftp engineer@10.20.21.10
```

Enter the lab password when prompted, then visit the HMI in a browser, submit a
setpoint, and check the historian console for the command topic. That produces
ordinary HTTP, MQTT, SSH, and SFTP traffic for your capture.

## Modbus TCP register map

Holding registers, using zero-based protocol addresses:

| Address | Meaning | Scale |
|---:|---|---|
| 0 | Process level | Engineering units × 10; 650 = 65.0% |
| 1 | Flow rate | Engineering units × 10; 120 = 12.0 units/s |
| 2 | Water quality | Engineering units × 10; 950 = 95.0% |
| 3 | Heartbeat counter | Increments once per second |

Coils 0 and 1 model a pump-run command and a valve-open command.

There is no hydraulic cause and effect: writing a register changes what the
device reports and nothing else. `TODO(Metropolis)`: define a realistic process
model and per-cell register maps before treating generated traffic as
operationally representative.

## Routine traffic, Telnet, DNS, and time

Four nodes exist to give the lab a believable baseline:

| Node | Address | What it does |
|---|---|---|
| `MET-CONTROL-CLIENT-01` | `10.20.21.20` | Every 30 seconds: resolves the PLC, requests NTP, reads four holding registers, writes register 0 and coil 0, then reads both back |
| `MET-LEGACY-GATEWAY-01` | `10.20.10.30:23/TCP` | Telnet host with a root shell and the lab password `LabOnly_Legacy_2026` |
| `MET-DNS-01` | `10.20.30.10:53/UDP,TCP` | Resolves local names such as `plc-intake.metropolis.test` |
| `MET-NTP-01` | `10.20.30.11:123/UDP` | Answers NTP requests |

Start DNS and NTP before the control client. The client carries explicit server
addresses, so it needs no resolver change. Its level writes repeat the sequence
650, 652, 654, 656, 658, and coil 0 is true for six cycles then false for two.
These are regular, bounded baseline commands; the PLC does not react to the coil.
Failed cycles log and retry. The NTP reply is observed, not used to set the clock.

Telnet credentials travel in clear text. Use the synthetic password only inside
this isolated testbed, and never bridge the legacy gateway to a production
network.

To check the services from a routed host:

```bash
dig @10.20.30.10 plc-intake.metropolis.test
chronyd -Q -t 5 'server 10.20.30.11 iburst'
telnet 10.20.10.30
```

Log in for Telnet as `root` with the inventory's password. Confirm the control
client logs successful cycles and that SCADA sees register 0 change. Record
capture points on the control-client and intake links.

## Attack lab

These nodes reproduce Gotham's attack structure against the Metropolis
inventory, so botnet and C2 lifecycle traffic appears in captures alongside
normal process traffic.

| Node | Address | Role |
|---|---|---|
| `MET-MIRAI-CNC-01` | `10.99.10.10` | Bot registry and operator console (control TCP 23, check-in TCP 48101) |
| `MET-MIRAI-LISTENER-01` | `10.99.10.11` | Accepts bot reports (TCP 48101) |
| `MET-MIRAI-LOADER-TFTP-01` | `10.99.10.12` | TFTP delivery role (UDP 69) |
| `MET-MIRAI-LOADER-WGET-01` | `10.99.10.13` | HTTP delivery role (TCP 80) |
| `MET-MIRAI-BOT-01` | `10.99.10.100` | Inert bot: checks in, probes an allow-list, reports |
| `MET-MERLIN-CNC-01` | `10.99.10.20` | C2 console (TCP 23) and agent channel (TCP 8443) |
| `MET-MERLIN-AGENT-01` | `10.99.10.21` | Inert agent: checks in, polls commands, uploads an artefact |

Console vocabulary follows Gotham. The Mirai console accepts `bots`, `use`,
`run`, `back`, and `exit`; the Merlin console accepts `listeners`, `use`, `set`,
`start`, `info`, `agent list`, `agent interact`, `run`, `back`, and `exit`.

| Image | Variables |
|---|---|
| `metropolis/mirai-cnc:dev` | `MIRAI_CNC_PORT=23`, `MIRAI_REPORT_PORT=48101`, `MIRAI_CNC_BIND=0.0.0.0` |
| `metropolis/mirai-scan-listener:dev` | `MIRAI_REPORT_PORT=48101`, `MIRAI_LISTENER_BIND=0.0.0.0` |
| `metropolis/mirai-loader:dev` | `LOADER_KIND=wget` or `tftp`, `LOADER_PORT` (defaults 80 or 69), `LOADER_BIND=0.0.0.0` |
| `metropolis/mirai-wget-loader:dev` | The same model pinned to `LOADER_KIND=wget`, `LOADER_PORT=80` |
| `metropolis/mirai-bot:dev` | Required `MIRAI_CNC_HOST`; `MIRAI_CNC_PORT=48101`, `MIRAI_LISTENER_HOST`, `MIRAI_REPORT_PORT=48101`, `BOT_ARCH`, `BOT_INTERVAL=30`, `BOT_TARGETS` (empty keeps the bot dormant) |
| `metropolis/merlin-cnc:dev` | `MERLIN_CONTROL_PORT=23`, `MERLIN_HTTP_PORT=8443`, `MERLIN_BIND=0.0.0.0` |
| `metropolis/merlin-agent:dev` | Required `MERLIN_CNC_URL`; `AGENT_ARCH`, `AGENT_SLEEP=20`, `AGENT_UPLOAD_ONCE=true` |

Two properties keep these safe to run:

- **No executable payload.** The loaders serve the text marker
  `metropolis-synthetic-payload-stub`, never a binary.
- **No arbitrary commands.** The Merlin agent simulates only `chmod`, `echo`,
  `id`, `uname`, `upload`, and `sleep`, and refuses everything else. The Mirai
  bot probes only the private addresses listed in `BOT_TARGETS`.

Both consoles record delivered commands without running them, so a capture shows
the control conversation while the testbed stays inert. To drive the whole
lifecycle, see the experiment toolkit's `run_mirai_choreography` and
`run_merlin` tools.

## Encrypted traffic

MQTT can run over TLS. It is off by default, so the plaintext lab behaves exactly
as before. CoAP is plaintext only; see the limitations below.

### MQTT over TLS

Generate the lab's own certificate authority and a broker certificate:

```bash
uv run python scripts/gen_lab_certificates.py --out-dir certs
```

That writes `certs/ca.crt`, `certs/server.crt`, and `certs/server.key`.
Certificates are lab material and the directory is Git-ignored; regenerate them
whenever you like.

The broker adds an encrypted listener on 8883 when `MQTT_TLS=true`, and needs the
certificate and key mounted in:

```text
MQTT_TLS=true
TLS_CERT_FILE=/mosquitto/certs/server.crt
TLS_KEY_FILE=/mosquitto/certs/server.key
TLS_CA_FILE=/mosquitto/certs/ca.crt
```

Clients reach it by pointing at port 8883 and trusting the CA:

```text
TLS=true
TLS_CA_FILE=/certs/ca.crt
TLS_INSECURE=false
```

The certificate is issued for the broker's GNS3 node name and its address, so a
client may reach it either way. `TLS_INSECURE=true` keeps the encryption but
stops checking the hostname — a lab convenience, not a safe default. The
certificate is validated rather than merely loaded: the tests run a real
handshake over loopback and confirm that a name the certificate does not cover
is refused.

The sensor, SCADA, the HMI, and the historian all read the same settings, so any
of them can connect to the encrypted listener. Add the three variables to a
node's environment and point it at port 8883. The settings come from one shared
module, `runtime/tls_config.py`, which every MQTT image copies in.

The experiment toolkit can also reach an encrypted broker:

```bash
python scripts/attacks/run.py mqtt_bruteforce_attack --tls --tls-ca certs/ca.crt
python scripts/attacks/run.py mqtt_flood_attack --tls --tls-ca certs/ca.crt
```

### CoAP has no DTLS

The CoAP service is plaintext. A DTLS path was written and removed because it
could not be made to complete a handshake, and a module that is present but
unverified invites a false claim. Adding it properly means driving the
datagram-oriented handshake loop to completion and proving it with a test that
runs a CoAP GET over DTLS. Until then, treat `coaps://` as unsupported.

## MQTT topics

| Topic | Producer | Who sees it |
|---|---|---|
| `metropolis/plant/intake/telemetry` | Sensor | Historian |
| `metropolis/scada/intake/status` | SCADA | Historian, HMI |
| `metropolis/plant/intake/command` | HMI | Historian; nothing consumes it |

The historian subscribes to `metropolis/#` and writes what it receives to its
console for capture.

The broker rejects anonymous connections and builds its password file at
startup from the lab-only environment values. The credentials in this repository
are public test fixtures — do not reuse them outside this isolated lab. Per-role
ACLs, TLS, and per-topic permissions remain future work, as do realistic
device-specific telemetry and alarm payload schemas.

## CoAP discovery and status

With `COAP_ENABLED=true` the sensor listens on UDP 5683:

- `coap://10.20.10.20:5683/.well-known/core` returns CoRE link-format discovery
  (Content-Format 40), advertising `</status>;rt="metropolis.sensor";ct=50`.
- `coap://10.20.10.20:5683/status` returns JSON (Content-Format 50) with
  `device`, `sensor`, and `value`. `/` is an alias.

Unknown URIs return `4.04`, unsupported methods `4.05`, and incompatible Accept
formats `4.06`. Malformed packets are ignored.

This is a small server supporting CON and NON GET requests. It has no DTLS,
Observe, blockwise transfer, discovery query filtering, or duplicate-request
cache, so it is not a complete CoAP stack.

## Image startup

Every model has unit and loopback coverage, but building an image and starting
its container is a step of its own. Rebuild after changing a model, and check a
node's console the first time you start it so an entrypoint failure is visible
rather than silent.
