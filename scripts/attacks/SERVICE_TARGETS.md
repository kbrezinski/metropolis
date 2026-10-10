# Metropolis service targets

What each service in the lab exposes, so you know what you are attacking and
what normal traffic looks like before you generate any.

The [experiment toolkit README](README.md) covers the tools. This page covers
the targets. Every address here comes from the
[device inventory](../../testbeds/metropolis/datasets/water_treatment_v1/device_instances/initial_devices.yaml)
and the [address plan](../../testbeds/metropolis/datasets/water_treatment_v1/topology/address-plan.yaml).

Start the nodes and confirm normal responses before you build an experiment on
top of them.

## Listeners

| Node | Address | Listener | Protocol |
|---|---|---|---|
| `MET-MQTT-BROKER-01` | `10.20.23.10` | TCP 1883 | MQTT, authenticated |
| `MET-PLC-INTAKE-01` | `10.20.10.10` | TCP 502 | Modbus TCP |
| `MET-RTU-RESERVOIR-01` | `10.20.14.10` | TCP 502 | Modbus TCP |
| `MET-RTU-RAW-WATER-01` | `10.20.15.10` | TCP 502 | Modbus TCP |
| `MET-SENSOR-INTAKE-01` | `10.20.10.20` | UDP 5683 | CoAP |
| `MET-HMI-INTAKE-01` | `10.20.50.10` | TCP 8080 | HTTP, Basic auth |
| `MET-ENGINEERING-01` | `10.20.21.10` | TCP 22 | SSH and SFTP |
| `MET-LEGACY-GATEWAY-01` | `10.20.10.30` | TCP 23 | Telnet |
| `MET-DNS-01` | `10.20.30.10` | UDP/TCP 53 | DNS |
| `MET-NTP-01` | `10.20.30.11` | UDP 123 | NTP |

Not every inventoried node listens. SCADA and the historian initiate their own
connections and accept none, so do not expect to find a service on them. RTSP is
not part of these images.

There is no dedicated availability target: measure an existing listener instead.

## Credentials

These are public lab fixtures. Do not reuse them anywhere else.

| Service | Account | Password |
|---|---|---|
| MQTT (device role) | `lab_device` | `LabOnly_Device_2026` |
| MQTT (operator role) | `lab_operator` | `LabOnly_MQTT_2026` |
| HMI | `operator` | `LabOnly_HMI_2026` |
| Engineering workstation | `engineer` | `LabOnly_Engineering_2026` |
| Legacy gateway (root) | `root` | `LabOnly_Legacy_2026` |

## MQTT

**Target:** `MET-MQTT-BROKER-01`, `10.20.23.10:1883/TCP`.

The broker rejects anonymous connections and builds its password file at
startup. Both accounts have the same permissions — their names do not establish
different roles, and there are no per-topic ACLs and no TLS listener.

The historian is an MQTT *client* at `10.20.22.10`, not a second broker, and the
sensor and HMI do not accept MQTT connections. Point MQTT tools at the broker.

Normal application topics:

| Topic | Producer | Consumer |
|---|---|---|
| `metropolis/plant/intake/telemetry` | `MET-SENSOR-INTAKE-01` | Historian console |
| `metropolis/scada/intake/status` | `MET-SCADA-01` | Historian console, HMI |
| `metropolis/plant/intake/command` | `MET-HMI-INTAKE-01` | Historian console; nothing acts on it |

Baseline telemetry is JSON. You can observe publications at the broker or
historian. The HMI command topic is **not** wired to PLC actuation, so publishing
there does not change the process.

## CoAP

**Target:** `MET-SENSOR-INTAKE-01`, `10.20.10.20:5683/UDP`. Requires
`COAP_ENABLED=true`, which is the default.

| URI | Normal response |
|---|---|
| `/.well-known/core` | `2.05 Content`, `application/link-format` (40), advertising `/status` |
| `/status` | `2.05 Content`, `application/json` (50), with device, sensor, and value |
| `/` | Alias for status |

Discovery follows [RFC 6690](https://www.rfc-editor.org/rfc/rfc6690.txt). Unknown
paths return `4.04`, unsupported methods `4.05`, and incompatible Accept formats
`4.06`. The server advertises only the status resource it implements and does not
pad responses to reach a particular ratio.

There is no setting to "enable amplification". Response expansion is something
you measure, and a reflection experiment additionally needs a source, a separate
victim, and a path that permits the traffic. Having a CoAP endpoint proves none
of that. The repository supplies only the normal responder.

The implementation is small: no DTLS, Observe, blockwise transfer, discovery
query filtering, or duplicate-request cache, though it does reject unsupported
critical options.

## Modbus

**Targets:** the PLC and RTUs on TCP 502.

SCADA reads the intake PLC's four holding registers — level, flow, quality, and
heartbeat — and coils represent pump and valve state. The
[register map](../../testbeds/metropolis/devices/README.md#modbus-tcp-register-map)
has the addresses and scaling.

`MET-CONTROL-CLIENT-01` at `10.20.21.20` performs a routine read/write cycle on
the intake PLC every 30 seconds, after a DNS lookup and an NTP sample. That gives
a capture a normal Modbus-write baseline, and independent DNS and NTP background
traffic. Pause it or record the values before a write experiment.

Use the correct unit ID, function, and datastore addresses. The process values
are synthetic: changing a register or coil changes what the device reports and
nothing else, and is not a model of damage to a water-treatment process.

## HMI

**Target:** `MET-HMI-INTAKE-01`, `http://10.20.50.10:8080/`.

The panel, status API, and setpoint API all require HTTP Basic authentication, so
an MQTT or Telnet module will not exercise this endpoint. The
[HMI section](../../testbeds/metropolis/devices/README.md#hmi-and-engineering-workstation)
documents the routes, required headers, payload validation, and response
semantics.

Capture ordinary browser polling and setpoint requests first. No vendor exploit
compatibility is claimed.

## SSH and Telnet

**SSH target:** `MET-ENGINEERING-01`, `10.20.21.10:22/TCP`, as `engineer`.
Real OpenSSH with a non-root shell and SFTP. Root login is disabled. Logs go to
container stderr.

**Telnet target:** `MET-LEGACY-GATEWAY-01`, `10.20.10.30:23/TCP`, as `root`. A
real Telnet login and container shell, useful for login traces and as a legacy
host. Credentials travel in clear text.

These are separate hosts, so a Telnet module will not work against the SSH
service. Use the account that matches the protocol.

## Botnet lifecycle

The attack-lab nodes at `10.99.10.0/24` reproduce the Mirai and Merlin control
planes: bots check in with a CNC, report to a scan listener, and fetch a payload
from a loader, while an operator lists the registry and dispatches commands.

| Node | Address | Role |
|---|---|---|
| `MET-MIRAI-CNC-01` | `10.99.10.10` | Bot registry and operator console |
| `MET-MIRAI-LISTENER-01` | `10.99.10.11` | Bot reports |
| `MET-MIRAI-LOADER-TFTP-01` | `10.99.10.12` | TFTP delivery |
| `MET-MIRAI-LOADER-WGET-01` | `10.99.10.13` | HTTP delivery |
| `MET-MIRAI-BOT-01` | `10.99.10.100` | Inert bot agent |
| `MET-MERLIN-CNC-01` | `10.99.10.20` | C2 console and agent channel |
| `MET-MERLIN-AGENT-01` | `10.99.10.21` | Inert agent |

Drive them with `run_mirai_choreography` and `run_merlin`. The
[attack lab section](../../testbeds/metropolis/devices/README.md#attack-lab)
documents the console vocabulary and safety properties.

This is a lab model, not a botnet. The loaders serve a text marker, the bot
connects only to an explicit private allow-list, and both consoles record
delivered commands without running them. Nothing is downloaded, forked, or
executed. Treat the captures as botnet-*shaped* traffic, not as evidence about
real Mirai behaviour.

Nothing routes between the attack lab and the process cells, so the lifecycle is
observable in isolation but cannot reach the PLC or HMI. Cross-zone reachability
needs router and firewall policy, which is still a `TODO`.

## Capturing an experiment

Capture on the GNS3 links that actually carry the traffic — the link to the
experiment source and the relevant service or victim. Two hosts on the same
subnet may never cross a router, so a backbone-only capture can miss them
entirely.

Record with each dataset:

- UTC start and end times
- Node, image, and tool versions
- Addressing and service configuration
- The capture point, and the normal baseline traffic from before the run
- The tool's JSONL output, so tool actions line up with the packets

Synchronise your capture hosts; ordinary Docker containers share their host's
clock. The Gotham CSV labelling utility does not label new Metropolis captures.

## Not built yet

These would help future experiments but do not exist and have no addresses:

| Addition | What it would give you |
|---|---|
| Health monitor and log collector | Consistent latency, failure, and recovery evidence |
| Dedicated receiver or victim node | A stable endpoint for experiments needing a separate receiving host |
| Optional RTSP device models | More application targets |
| Firewall and IPsec policy | Containment, rather than reachability, between zones |

For Gotham methodology and attribution, see the
[Gotham paper](https://doi.org/10.1109/TDSC.2023.3247166).
