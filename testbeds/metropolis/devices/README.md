# Device reference

Reusable Docker models for Metropolis. Each node gets its own identity,
address, and service settings from the selected dataset's
`device_instances/initial_devices.yaml`.

For image builds and GNS3 startup, follow the
[GNS3 setup guide](../../../scripts/gns3/README.md).
This page describes device behavior and configuration.

## The image set

Paths below are relative to this directory.

| Model | Source | Main behavior |
|---|---|---|
| PLC / RTU | `controllers/plc/` | Modbus TCP controller with selectable cell profile |
| Pump drive / valve | `field/actuators/` | Simplified cyclic EtherNet/IP I/O |
| Analyzer | `field/analyzers/` | Modbus pH/turbidity or chlorine residual |
| Pressure instrument | `field/instruments/` | Modbus differential pressure and heartbeat |
| Level instrument | `field/level_instrument/` | Modbus level and heartbeat |
| Sensor | `field/sensors/` | MQTT telemetry and CoAP resources |
| MQTT broker | `services/mqtt_broker/` | Authenticated Mosquitto |
| SCADA | `operations/scada/` | Controller polling and HMI command/readback |
| HMI | `operations/hmi/` | Authenticated HTTP panel using MQTT |
| Historian | `operations/historian/` | MQTT subscription and console records |
| Engineering workstation | `operations/engineering_workstation/` | OpenSSH shell and SFTP; routed FreeRDP client during captures |
| RDP server | `operations/rdp_server/` | Linux xrdp desktop on TCP 3389 plus a separate synthetic UDP 3389 responder |
| RDP client | `operations/rdp_client/` | Headless FreeRDP sessions initiated by experiment runs |
| Control client | `operations/control_client/` | Routine DNS, NTP, Modbus, and SNMP traffic |
| Legacy gateway | `field/legacy_gateway/` | BusyBox Telnet with a lab root login |
| DNS / NTP | `services/dns/`, `services/ntp/` | Local name/time services; NTP node also receives syslog |
| Open vSwitch | `infrastructure/openvswitch/` | VLAN access/trunk switching |
| Reachability probe | `field/reachability_probe/` | Idle host with no service |
| Synthetic Mirai / Merlin | `attack/mirai/`, `attack/merlin/` | Inert bot/agent and control services |

These models generate protocol traffic. Their process values are simplified;
they do not constitute vendor firmware, a complete industrial protocol stack,
or a physical water-treatment simulation.

## Node configuration

The GNS3 builder supplies the inventory environment to new nodes. For a manually
created node, set the same values in its Docker template/node configuration:

```text
NODE_HOSTNAME=MET-PLC-INTAKE-01
NODE_IP=10.20.10.10/24
NODE_GATEWAY=10.20.10.1
NODE_INTERFACE=eth0
```

The shared entrypoint waits up to 30 seconds for the interface, sets its address
and default route, then starts the service. Connect the interface before
startup. An empty `NODE_IP` skips network setup; a gateway without an IP is an
error. Each model configures one IPv4 interface and gateway. Open vSwitch uses
its own startup path.

Services listen on their GNS3 interfaces; Docker host-port publishing is not
needed. Rebuild changed images on the hosting compute and update reused nodes'
environments. The builder does not reconcile old image/environment settings.

### Controller profiles and timing

The controller image defaults to intake behavior. Other roles set `CELL_ROLE`:

| Profile | Field peers | Main configuration |
|---|---|---|
| Intake | Duty/standby drives and valve | `EIP_DUTY_HOST`, `EIP_STANDBY_HOST`, `EIP_VALVE_HOST` |
| `dosing` | Analyzer and duty/standby drives | `ANALYZER_HOST`, `ANALYZER_POLL_INTERVAL`, `DOSING_PH_LOW/HIGH`, drive hosts |
| `filtration` | Pressure instrument, pump, valve | `INSTRUMENT_HOST`, `DP_POLL_INTERVAL`, `BACKWASH_SECONDS`, `EIP_PUMP_HOST`, `EIP_VALVE_HOST` |
| `disinfection` | Residual analyzer and duty/standby pumps | `ANALYZER_HOST`, `ANALYZER_POLL_INTERVAL`, drive hosts |
| `remote_site` | Level instrument and pump | `REMOTE_SITE_ROLE=reservoir\|raw_water`, `INSTRUMENT_HOST`, `LEVEL_POLL_INTERVAL`, `EIP_PUMP_HOST` |

Nominal defaults are one-second instrument polling, 100 ms cyclic I/O
(`EIP_RPI_MS`), and five-second central SCADA polling (`POLL_INTERVAL`).
The inventory holds the actual settings and target addresses.

### Other service settings

| Model | Common settings |
|---|---|
| Actuator | `ACTUATOR_ROLE=pump_drive\|valve_controller`, `ACTUATOR_FAULTED`, `EIP_PORT=44818`, `EIP_IO_PORT=2222` |
| Analyzer | `ANALYZER_ROLE=dosing\|chlorine`, `ANALYZER_INTERVAL=1`, `MODBUS_PORT=502` |
| Pressure / level | `DP_UPDATE_INTERVAL` / `LEVEL_UPDATE_INTERVAL`, minimum/maximum readings, cycle duration |
| Sensor | `PLC_HOST`, `SENSOR_REGISTER`, `SENSOR_DIVISOR`, `MQTT_TOPIC`, `PUBLISH_MODE=persistent\|per-message`, `COAP_ENABLED` |
| MQTT clients | `MQTT_HOST`, `MQTT_PORT`, unique `MQTT_USERNAME` / `MQTT_PASSWORD`, `MQTT_ACCESS_ROLE` |
| SCADA | `SCADA_TARGETS` JSON entries with `id`, `host`, `profile`, `topic`; `DNP3_TARGETS_B64` from named inventory peers; `PLC_STALE_SECONDS=15`, `COMMAND_MAX_AGE_SECONDS=15` |
| Remote RTU | `DNP3_ENABLED=true`, `DNP3_PORT=20000` |
| HMI | Required `HMI_PASSWORD`; `HMI_USERNAME=operator`, `HMI_HTTP_PORT=8080`, `HMI_CELL_ID`, topic settings, `COMMAND_ACK_TIMEOUT=15`, `TELEMETRY_STALE_SECONDS=30` |
| Historian | `MQTT_TOPIC=metropolis/#` |
| Engineering | Required `ENGINEER_PASSWORD`; fixed username `engineer`, TCP 22 |
| Control client | `PLC_DNS_NAME`, `DNS_SERVER`, `NTP_SERVER`, `CONTROL_INTERVAL=30`, `SNMP_TARGETS`, `COAP_TARGET_NODE` |
| Legacy gateway | Required `LEGACY_ROOT_PASSWORD`, TCP 23 |

SCADA without `SCADA_TARGETS` retains its single-intake `PLC_HOST` /
`MQTT_TOPIC` behavior. The builder resolves `DNP3_PEER_NODES` and
`COAP_TARGET_NODE` to addresses from the selected inventory. The broker rejects
anonymous clients and generates its password and topic-ACL files from that
inventory. Bundled passwords are public lab fixtures.
Client images require the inventoried MQTT username and password; they have no
shared credential fallback.

## Modbus TCP register map

Holding-register addresses are **zero-based**. All controller profiles expose
registers 0–5:

| Profile | 0 | 1 | 2 | 3 | 4 (writable) | 5 |
|---|---|---|---|---|---|---|
| Intake | Level × 10 % | Flow × 10 | Quality × 10 % | Heartbeat | Level request × 10 % | Alarms |
| Dosing | pH × 100 | Turbidity × 10 | Confirmed dose × 10 % | Heartbeat | Dose request × 10 % | Alarms |
| Filtration | Pressure × 10 kPa | Phase | Confirmed output bits | Heartbeat | Threshold × 10 kPa | Alarms |
| Disinfection | Residual × 10 mg/L | Pump status bits | On/off output | Heartbeat | Residual target × 10 mg/L | Alarms |
| Remote RTU | Level × 10 % | Pump running | Instrument ready | Heartbeat | Requested pump state | Alarms |

Alarm bits: 1 instrument unavailable, 2 instrument stale, 4 duty/pump
unavailable, 8 standby unavailable, 16 valve unavailable, 32 output unconfirmed.
Not every profile uses every bit.

Intake coils 0 and 1 request pump and valve operation. Dosing, filtration, and
disinfection use coil 0 to enable their automatic requests; remote RTUs use it
to request pump operation. Intake discrete inputs 0–2 expose confirmed duty,
standby, and valve state. Other plant profiles report their field/output
readiness through their profile-specific inputs and status registers.

| Instrument | Holding registers |
|---|---|
| Dosing analyzer | 0 pH × 100, 1 turbidity × 10, 2 heartbeat |
| Chlorine analyzer | 0 residual × 10 mg/L, 1 heartbeat |
| Pressure instrument | 0 differential pressure × 10 kPa, 1 heartbeat |
| Level instrument | 0 level × 10 %, 1 heartbeat |

Cell behavior:

- Intake uses confirmed pump/valve states in its small coupled process model.
- Dosing uses pH hysteresis, requests duty then standby, and stops requests for
  failed or stale analyzer data.
- Filtration opens the backwash valve before requesting its pump. Defaults:
  65 kPa threshold, 8-second backwash, and a 10 kPa rearm band.
- Disinfection requests duty then standby when residual is below its target.
- Remote RTUs poll their local level instrument and command their local pump.
  SCADA reads their status without the plant HMI command path.

Instrument readings vary independently of actuator commands in the newer cells
and remote sites. A successful register write confirms stored simulator state,
not a physical process effect.

## Cyclic EtherNet/IP

Controllers establish I/O connections on TCP 44818, then exchange command/status
assemblies with actuators on UDP 2222 at `EIP_RPI_MS` (default 100 ms).
Standby drives exchange status even when idle. Output-update loss stops the
actuator after at least four requested intervals, with a minimum timeout of
0.5 seconds. `ACTUATOR_FAULTED=true` can exercise a duty-drive fault.

The assemblies carry on/off commands and active/fault status. Explicit CIP
Get/Set Attribute Single on lab class `0x64` remains available for diagnostics.
This is a simplified lab model, not a vendor profile or ODVA-conformant stack.

PLC-to-instrument and PLC-to-actuator traffic stays on the local cell/site LAN.
Central SCADA polls cross routed links. Capture field attachments as well as
uplinks to observe both paths.

## DNP3 remote-site events

The two remote RTUs run lab DNP3 outstations on TCP 20000. SCADA takes an
integrity snapshot on connection and every 60 seconds, and polls class-1 alarm
events every five seconds on a separate reconnecting worker. Modbus continues
to carry the existing routine values and attack-facing registers. This is a
small solicited-poll model, not a certified DNP3 implementation or secure
authentication profile. Capture `attach-scada`, each RTU attachment, and the
WAN uplinks for the routed path.

## HMI and engineering workstation

The four plant HMIs use HTTP Basic authentication on TCP 8080.
The engineering workstation provides SSH/SFTP on TCP 22 and can initiate an
RDP session during an experiment. The reference dataset also has an operations
RDP client. Both clients connect to servers on different VLANs, so each path
crosses a router firewall. `experiment_run` starts one session on each path
after capture begins; no session runs merely because a client node is online.
The server runs real `xrdp` over TCP 3389. UDP 3389 is a separate synthetic
request/reply service, benign by default and temporarily amplifying only when
selected for a run. It must not be interpreted as Microsoft RDP-over-UDP.
Get addresses and lab passwords from the inventory.

### HMI command path

An operator request follows HMI → MQTT broker → SCADA → PLC register 4.
SCADA reads the register back and publishes an acknowledgement.

| Endpoint/state | Meaning |
|---|---|
| `GET /`, `GET /api/status` | Panel and current status |
| `GET /healthz` | HTTP availability and MQTT connection state |
| `POST /api/setpoint` | JSON `{"value":65}`, content type `application/json`, header `X-Metropolis-Request: hmi` |
| HTTP 202 | Accepted by the HMI; controller confirmation is still pending |
| `stored` | PLC register readback matched |
| `rejected`, `failed`, `timeout` | Request was not confirmed |
| HTTP 409 | Another command is still pending |

Only an explicit operator submission publishes a request. Panel state resets
on restart. Match `COMMAND_MAX_AGE_SECONDS` to `COMMAND_ACK_TIMEOUT` when
changing timing. MQTT credentials do not authenticate a payload's `device`
field. Broker ACLs restrict each HMI to its own command topic, SCADA to its
declared commands/status, the sensor to telemetry publishing, and the
historian to reading. Rebuild or refresh existing GNS3 nodes when inventory
MQTT settings change; the builder reports stale settings.

### Engineering workstation

OpenSSH provides a real non-root shell and SFTP. Root login, forwarding, and
sudo are disabled. Each node generates host keys in `/var/lib/metropolis/ssh`;
persist that directory separately per node if stable host identity is needed.

## Routine traffic, Telnet, DNS, and time

The control client performs DNS and NTP requests, Modbus reads/writes and
readbacks, read-only router SNMP uptime polls, and a CoAP `/status` GET each
control cycle.
`CONTROL_INTERVAL` defaults to 30 seconds. Its NTP reply is observed without
setting the clock.

DNS serves local records without upstream forwarding. NTP replies use the host
clock; its node also runs the UDP 514 syslog receiver. The legacy gateway
provides an intentionally plaintext Telnet root shell for isolated lab use.

## Telemetry produced by runnable images

| Source | Available output and limits |
|---|---|
| Controllers, instruments, sensors, SCADA, HMI, historian, control client | Startup, operation, and errors on stdout/stderr; CoAP also emits structured request audit records |
| Mosquitto | Startup, connection, and authentication notices |
| OpenSSH | Startup/failure events and accepted/failed authentication on stderr; no password/key logging |
| RDP servers and clients | Session and mode outcomes on container output; synthetic UDP is labelled separately from xrdp |
| Telnet | Startup events; BusyBox does not reliably emit individual login outcomes |
| DNS | dnsmasq startup and queries |
| NTP | chronyd startup/synchronization warnings; not one line per client request |
| VyOS | Network syslog to the NTP/syslog receiver |
| Open vSwitch | OVS daemon files bridged through Docker stdout |
| Synthetic attack nodes | Lifecycle, command, report, and upload events |
| Reachability probe | Intentionally silent |

`experiment_run` saves source status and missing-source failures. Container
output is simulator diagnostics. Router syslog travels over UDP; switch events
use the separate `docker_stdout_bridge` transport and require compute access.
Neither a quiet service nor a successful collector proves that every network
event has a log. See the [run/log guide](../../../scripts/gns3/README.md#experiment-device-logs)
for files and schemas.

## Attack lab

Mirai and Merlin models produce synthetic control traffic. Loaders serve a text
marker, and the inert agents do not execute arbitrary commands. Their nodes
and addresses are defined in the inventory.

| Image | Main settings |
|---|---|
| Mirai CNC | `MIRAI_CNC_PORT=23`, `MIRAI_REPORT_PORT=48101` |
| Mirai listener | `MIRAI_REPORT_PORT=48101` |
| Mirai loader | `LOADER_KIND=wget\|tftp`, `LOADER_PORT` (80 or 69) |
| Mirai bot | `MIRAI_CNC_HOST`, `MIRAI_CNC_PORT` (bot channel), `BOT_INTERVAL=5`, optional `BOT_TARGETS` allow-list |
| Merlin CNC | `MERLIN_CONTROL_PORT=23`, `MERLIN_HTTP_PORT=8443`, `MERLIN_TLS_CERT_FILE`, `MERLIN_TLS_KEY_FILE` |
| Merlin agent | `MERLIN_CNC_URL=https://...`, `MERLIN_CA_FILE`, `AGENT_SLEEP=20` |

The bot remains online but only probes a private lab target after receiving a
scenario task. An empty `BOT_TARGETS` means no additional target restriction;
it does not enable automatic scanning. The Merlin agent verifies the C&C's
HTTPS certificate and accepts only its inert command vocabulary. Its marker
transfer and `chmod` are limited to a fixed text file, with no shell execution.
Use the [attack toolkit](../../../scripts/attacks/README.md#mirai-and-merlin-scenarios)
for scenarios and external inert-agent contracts.

## Encrypted traffic

MQTT TLS is optional and off by default. Generate lab certificates from the
repository root:

```bash
uv run python scripts/gen_lab_certificates.py --out-dir certs
```

Mount the generated files into the appropriate nodes and configure:

| Broker | MQTT clients |
|---|---|
| `MQTT_TLS=true` | `TLS=true` |
| `TLS_CERT_FILE=/mosquitto/certs/server.crt` | `TLS_CA_FILE=/certs/ca.crt` |
| `TLS_KEY_FILE=/mosquitto/certs/server.key` | `MQTT_PORT=8883` |
| `TLS_CA_FILE=/mosquitto/certs/ca.crt` | `TLS_INSECURE=false` |

The certificate must cover the hostname/address clients use. Sensor, SCADA,
HMI, and historian share this configuration. Attack MQTT clients expose
`--tls` and `--tls-ca`. CoAP has no DTLS support.

## MQTT topics

The bundled plant cell IDs are `intake`, `dosing`, `filtration`, and
`disinfection`. Inventory settings can select other topics.

| Topic pattern | Producer → consumer |
|---|---|
| `metropolis/plant/intake/telemetry` | Sensor → historian |
| `metropolis/scada/<cell>/status` | SCADA → HMI and historian |
| `metropolis/plant/<cell>/command` | HMI → SCADA and historian |
| `metropolis/scada/<cell>/ack` | SCADA → HMI and historian |
| `metropolis/scada/reservoir/status`, `metropolis/scada/raw-water/status` | SCADA → historian |

The historian subscribes to `metropolis/#`; the lab operator account can only
publish under `metropolis/experiment/#`. Plain MQTT and HTTP expose lab
credentials on the network; use only the intended isolated environment.

## CoAP discovery and status

With `COAP_ENABLED=true`, the sensor serves UDP 5683:

- `/.well-known/core`: CoRE discovery, Content-Format 40.
- `/status` and `/`: JSON status, Content-Format 50.
- `/level`, `/flow`, `/quality`, `/pressure`, `/temperature`,
  `/turbidity`, `/pump`: text values when available.

Each response emits a `COAP_AUDIT` record with resource, code, byte counts,
peer, and token-derived experiment ID. Unknown resources return 4.04,
unsupported methods 4.05, and incompatible Accept formats 4.06. Malformed
packets are ignored. This CON/NON GET server has no Observe, blockwise transfer,
discovery query filtering, duplicate-request cache, application authentication,
or DTLS. A reachable client can read its resources without credentials.
