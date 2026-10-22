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
| Networked actuator | `field/actuators/` | EtherNet/IP cyclic I/O pump drive or valve controller | Plant VLANs 10–13 or remote-site LANs 14–15 |
| Networked analyzer | `field/analyzers/` | Modbus TCP pH/turbidity or chlorine residual readings, selected by `ANALYZER_ROLE` | Dosing VLAN 11 or disinfection VLAN 13 |
| Differential-pressure instrument | `field/instruments/` | Modbus TCP pressure drop and heartbeat with a repeatable waveform | Filtration VLAN 12, `10.20.12.0/24` |
| Level instrument | `field/level_instrument/` | Modbus TCP level and heartbeat with a simple independent variation | Reservoir and raw-water LANs, `10.20.14.0/24` and `10.20.15.0/24` |
| MQTT and CoAP sensor | `field/sensors/` | Publishes MQTT telemetry; answers CoAP discovery and status on UDP 5683 | Plant or remote-site LAN |
| MQTT broker | `services/mqtt_broker/` | Authenticated Mosquitto broker for telemetry and HMI commands | VLAN 23, `10.20.23.0/24` |
| SCADA collector | `operations/scada/` | Polls configured Modbus controllers, publishes MQTT snapshots, and writes acknowledged HMI setpoints to PLCs | VLAN 20, `10.20.20.0/24` |
| HMI panel | `operations/hmi/` | Authenticated HTTP panel with MQTT telemetry, operator requests, and controller readback status | HMI VLANs 50-53 |
| Historian | `operations/historian/` | Subscribes to MQTT and logs received events to its console | VLAN 22, `10.20.22.0/24` |
| Engineering workstation | `operations/engineering_workstation/` | Real OpenSSH shell and SFTP, with Python Modbus and MQTT libraries | VLAN 21, `10.20.21.0/24` |
| Routine control client | `operations/control_client/` | Resolves the PLC by DNS, samples NTP, and periodically reads and writes Modbus | VLAN 21, `10.20.21.0/24` |
| Legacy gateway | `field/legacy_gateway/` | Telnet login and shell, for experiments that need legacy host access | Intake VLAN 10 |
| DNS | `services/dns/` | Local records for this dataset; no upstream forwarding | OT services VLAN 30 |
| NTP | `services/ntp/` | Local time replies, using the host clock without changing it | OT services VLAN 30 |
| Open vSwitch | `infrastructure/openvswitch/` | Managed Layer 2 switching with declared trunk and access VLANs | All switch nodes |
| Reachability probe | `field/reachability_probe/` | A minimal host that runs no service, so each zone has something to ping or route to | Any zone that needs a positive control |
| Mirai chain | `attack/mirai/` | Command console, scan listener, loaders, and an inert bot agent | Attack lab `10.99.10.0/24` |
| Merlin C2 | `attack/merlin/` | C2 console and an inert agent | Attack lab `10.99.10.0/24` |

The intake PLC commands a duty pump drive, a standby pump drive, and a valve
controller as separate GNS3 Docker nodes. The dosing PLC polls a networked
analyzer and commands separate duty and standby drives. The filtration PLC
polls a networked differential-pressure instrument and commands a backwash
pump and valve. The disinfection PLC polls a chlorine residual analyzer and
commands duty and standby metering pumps. All four cells use cyclic EtherNet/IP
I/O. At the reservoir and raw-water sites, each RTU polls a separate networked
level instrument and exchanges cyclic I/O with a pump drive. The shared
controller image selects `CELL_ROLE=dosing`, `filtration`, `disinfection`, or
`remote_site` for these roles. The shared analyzer image uses
`ANALYZER_ROLE=chlorine` for disinfection and defaults to dosing pH/turbidity.
Chlorine and remote-site level readings do not depend on pump commands; these
are traffic models rather than physical process models.
Experiment runs collect Docker output as simulator diagnostics with
`scripts/gns3/run.py experiment_run`.

## Build the images

Run these from the repository root. The build context is the root, so the
Dockerfiles can copy the shared model code.

```bash
docker build -f testbeds/metropolis/devices/controllers/plc/Dockerfile -t metropolis/controller:dev .
docker build -f testbeds/metropolis/devices/field/actuators/Dockerfile -t metropolis/actuator:dev .
docker build -f testbeds/metropolis/devices/field/analyzers/Dockerfile -t metropolis/analyzer:dev .
docker build -f testbeds/metropolis/devices/field/instruments/Dockerfile -t metropolis/dp-instrument:dev .
docker build -f testbeds/metropolis/devices/field/level_instrument/Dockerfile -t metropolis/level-instrument:dev .
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
docker build -f testbeds/metropolis/devices/infrastructure/openvswitch/Dockerfile -t metropolis/openvswitch:dev .
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

Disinfection reuses the controller, analyzer, actuator, HMI, and SCADA images.
The remote sites reuse controller, actuator, and SCADA and add the level
instrument image. Rebuild changed images on the GNS3 compute that hosts those
nodes before starting the lab.

## How a node configures itself

Every end-device image uses `devices/runtime/network-entrypoint.sh` as its entrypoint.
The Open vSwitch infrastructure image starts its own OVS services instead.
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
| `metropolis/controller:dev` | `DEVICE_ROLE=PLC`, `MODBUS_HOST=0.0.0.0`, `MODBUS_PORT=502`; intake uses `PROCESS_LEVEL`, `PROCESS_FLOW`, `PROCESS_QUALITY`, `EIP_DUTY_HOST`, `EIP_STANDBY_HOST`, `EIP_VALVE_HOST`; dosing uses `CELL_ROLE=dosing`, `ANALYZER_HOST`, `ANALYZER_POLL_INTERVAL=1`, `DOSING_PH_LOW=700`, `DOSING_PH_HIGH=735`, `EIP_DUTY_HOST`, `EIP_STANDBY_HOST`; filtration uses `CELL_ROLE=filtration`, `INSTRUMENT_HOST`, `DP_POLL_INTERVAL=1`, `BACKWASH_SECONDS=8`, `EIP_PUMP_HOST`, `EIP_VALVE_HOST`; disinfection uses `CELL_ROLE=disinfection`, `ANALYZER_HOST`, `ANALYZER_POLL_INTERVAL=1`, `EIP_DUTY_HOST`, `EIP_STANDBY_HOST`; remote RTUs use `CELL_ROLE=remote_site`, `REMOTE_SITE_ROLE=reservoir` or `raw_water`, `INSTRUMENT_HOST`, `EIP_PUMP_HOST`, and `LEVEL_POLL_INTERVAL=1`; cyclic roles use `EIP_RPI_MS=100`, `EIP_IO_PORT=2222` |
| `metropolis/actuator:dev` | `ACTUATOR_ROLE=pump_drive\|valve_controller`, `ACTUATOR_FAULTED=false`, `EIP_PORT=44818`, `EIP_IO_PORT=2222` |
| `metropolis/analyzer:dev` | `ANALYZER_ROLE=dosing` (default) exposes pH, turbidity, heartbeat; `ANALYZER_ROLE=chlorine` exposes residual x10 mg/L and heartbeat. Also uses `MODBUS_HOST=0.0.0.0`, `MODBUS_PORT=502`, `ANALYZER_INTERVAL=1`. |
| `metropolis/dp-instrument:dev` | `MODBUS_HOST=0.0.0.0`, `MODBUS_PORT=502`, `DP_UPDATE_INTERVAL=1`, `DP_MIN_X10=250`, `DP_MAX_X10=800`, `DP_CYCLE_SECONDS=120` |
| `metropolis/level-instrument:dev` | `MODBUS_HOST=0.0.0.0`, `MODBUS_PORT=502`, `LEVEL_UPDATE_INTERVAL=1`, `LEVEL_MIN_X10`, `LEVEL_MAX_X10`, `LEVEL_CYCLE_SECONDS` |
| `metropolis/mqtt-sensor:dev` | `SENSOR_TYPE`, `SENSOR_VALUE`, `PLC_HOST`, `SENSOR_REGISTER`, `SENSOR_DIVISOR`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_HOST`, `MQTT_PORT=1883`, `MQTT_TOPIC`, `PUBLISH_MODE=persistent\|per-message`, `COAP_ENABLED=true`, `COAP_PORT=5683` |
| `metropolis/mqtt-broker:dev` | Builds its password file at startup. Synthetic accounts: `lab_device` / `LabOnly_Device_2026` and `lab_operator` / `LabOnly_MQTT_2026`. Listens on TCP 1883. |
| `metropolis/scada:dev` | `SCADA_TARGETS` is a JSON array of `{id, host, profile, topic}` entries; plant profiles `intake`, `dosing`, `filtration`, and `disinfection` support HMI commands, while `reservoir` and `raw_water` are read-only RTU profiles. Also uses `MODBUS_PORT=502`, `MODBUS_UNIT_ID=1`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_HOST`, `MQTT_PORT`, `POLL_INTERVAL=5`, `PLC_STALE_SECONDS=15`, `COMMAND_MAX_AGE_SECONDS=15`. Without `SCADA_TARGETS`, `PLC_HOST` and `MQTT_TOPIC` retain single-intake behavior. |
| `metropolis/hmi:dev` | Required `HMI_PASSWORD`; `HMI_USERNAME=operator`, `HMI_HTTP_HOST=0.0.0.0`, `HMI_HTTP_PORT=8080`, `MQTT_USERNAME`, `MQTT_PASSWORD`, `MQTT_HOST`, `MQTT_PORT`, `MQTT_TOPIC`, `MQTT_TELEMETRY_TOPIC`, `MQTT_ACK_TOPIC`, `SETPOINT`, `HMI_CELL_ID`, `HMI_PANEL_TITLE`, `HMI_SETPOINT_LABEL`, `HMI_SETPOINT_COMMAND`, `HMI_SETPOINT_UNIT`, `HMI_SETPOINT_MIN/MAX`, `COMMAND_ACK_TIMEOUT=15`, `TELEMETRY_STALE_SECONDS=30` |
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
| `MET-HMI-DOSING-01` | `http://10.20.51.10:8080/` | `operator` / `LabOnly_HMI_2026` |
| `MET-HMI-FILTRATION-01` | `http://10.20.52.10:8080/` | `operator` / `LabOnly_HMI_2026` |
| `MET-HMI-DISINFECTION-01` | `http://10.20.53.10:8080/` | `operator` / `LabOnly_HMI_2026` |
| `MET-ENGINEERING-01` | SSH and SFTP at `10.20.21.10:22` | `engineer` / `LabOnly_Engineering_2026` |

Attach the engineering node to VLAN 21 and the four HMIs to VLANs 50 through
53 respectively. If an engineering template carries a start-command
override such as `sleep infinity`, clear it so the image's SSH startup runs.

### HMI

Each panel shows its configured SCADA topic and accepts a setpoint in 0.1-unit
increments. Intake and dosing use 0–100%; filtration uses a 10–100 kPa
backwash threshold; disinfection uses a 0–10 mg/L residual target. Only an
explicit operator POST publishes a command. SCADA
subscribes to the cell command topic, validates the request, writes PLC holding
register 4 over Modbus TCP, reads it back, and publishes a correlated MQTT
acknowledgement. `stored` confirms the register value; it does not confirm a
physical output. The dosing PLC uses the requested percent in its drive logic;
the filtration PLC uses the threshold to trigger backwash; the disinfection PLC
compares its analyzer reading with the target to request pump operation. The
intake PLC stores the level request but keeps its existing manual coil control
behavior.

All routes require HTTP Basic authentication: `/`, `/api/status`, `/healthz`,
and `POST /api/setpoint`. A setpoint request is a JSON body of `{"value":65}`
with `Content-Type: application/json` and an `X-Metropolis-Request: hmi`
header. A `202` means the request was accepted locally. The panel separately
reports MQTT queueing and `pending`, `stored`, `rejected`, `failed`, or `timeout`
controller status, matched by request ID and cell. `/healthz` reports HTTP
availability and the MQTT connection flag separately. Telemetry is marked
fresh, stale, or unavailable. Panel state is in memory and resets on restart.
The panel accepts one outstanding command at a time; a second POST gets `409`
until the first is acknowledged or times out. Match SCADA's
`COMMAND_MAX_AGE_SECONDS` to the HMI's `COMMAND_ACK_TIMEOUT` when changing the
defaults so a delayed command cannot be applied after the panel times out.
The shared lab MQTT credentials do not authenticate the `device` field in a
command payload; use a broker identity/ACL design before treating that field as
strong provenance.

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
setpoint, then check the panel for a controller readback and the historian
console for the command and acknowledgement topics. That produces ordinary
HTTP, MQTT, Modbus TCP, SSH, and SFTP traffic for your capture.

## Modbus TCP register map

Holding registers, using zero-based protocol addresses:

| Address | Meaning | Scale |
|---:|---|---|
| 0 | Process level | Engineering units × 10; 650 = 65.0% |
| 1 | Flow rate | Engineering units × 10; 120 = 12.0 units/s |
| 2 | Water quality | Engineering units × 10; 950 = 95.0% |
| 3 | Heartbeat counter | Increments once per second |
| 4 | Writable HMI setpoint | Engineering units × 10: intake level %, dosing output %, filtration backwash threshold kPa, or disinfection residual target mg/L |
| 5 | Alarm bitmask | 1 instrument unavailable, 2 instrument stale, 4 duty/pump unavailable, 8 standby unavailable, 16 valve unavailable, 32 output unconfirmed |

Coils 0 and 1 are the pump-run and valve-open commands. On the intake PLC,
the controller sends these commands over EtherNet/IP to `10.20.10.40` (duty
drive), `.41` (standby drive), and `.42` (valve controller). If the duty drive
does not respond, the standby drive is commanded to run. The process model uses
confirmed pump and valve state. All three nodes share the PLC's existing
`10.20.10.0/24` VLAN; no additional route is needed. Set
`ACTUATOR_FAULTED=true` on the duty drive to exercise standby takeover. The
PLC opens an I/O connection on TCP 44818, then sends a one-byte command assembly
to each actuator over UDP 2222 at the configurable `EIP_RPI_MS` interval
(default 100 ms). Each actuator produces an active/fault status assembly at
the same interval. The standby drive exchanges status while idle and receives
a run command when the duty drive is unavailable or reports that it is not
running. Loss of output updates makes an actuator stop after at least four
requested intervals (minimum 0.5 s). Discrete inputs 0–2 on the PLC expose
confirmed duty, standby, and valve state to SCADA. Existing CIP Get/Set
Attribute Single messages on lab-specific class `0x64` remain available for
diagnostics. The I/O assemblies and connection handling are simplified for
this simulator; they are not a vendor drive profile or an ODVA-conformant
implementation. This follows [ODVA's description of unconnected TCP setup and
cyclic UDP I/O](https://www.odva.org/publication_download/ethernet-ip-technology-overview/).
Rebuild the controller and actuator images after changing
`EIP_RPI_MS` or the simulator code. Live GNS3 timing and packet captures still
need to be checked on the user's GNS3 host.

The intake controller's coupled process model changes level, flow, and quality
over time. It is a traffic-generating approximation, not a hydraulic simulation.

### Dosing cell

`MET-PLC-DOSING-01` (`10.20.11.10`) polls the networked analyzer
`MET-ANALYZER-DOSING-01` (`10.20.11.20`) over Modbus TCP every second. The
analyzer serves pH, turbidity, and a heartbeat in holding registers 0–2; its
configurable waveform changes the readings without simulating chemistry. The
PLC exposes pH × 100, turbidity × 10, confirmed dose output × 10, and its own
heartbeat in holding registers 0–3, with the requested output in register 4
and alarms in register 5. Coil 0 enables dosing. Discrete inputs 0–2 report
confirmed duty drive, standby drive, and analyzer availability.

The PLC applies pH hysteresis (`DOSING_PH_LOW=700`, `DOSING_PH_HIGH=735` in
hundredths of a pH unit). It requests the duty drive when dosing is needed,
then requests the standby drive if the duty drive is unavailable or does not
confirm running. It stops both requests when the analyzer read fails or its
heartbeat freezes for `ANALYZER_STALE_SECONDS` (default 4 seconds). A confirmed
running drive applies the HMI output setpoint; otherwise output reads zero. Both
drives use the same simplified TCP 44818 connection setup and cyclic UDP 2222
assemblies described for intake. The analyzer and both drives stay on dosing
VLAN 11; SCADA in VLAN 20 polls the PLC over Modbus TCP. The dosing HMI is in
VLAN 51 and subscribes to `metropolis/scada/dosing/status`; its MQTT command
reaches the PLC through SCADA and gets a register-readback acknowledgement.

The [inventory](../datasets/water_treatment_v1/device_instances/initial_devices.yaml)
holds addresses and the SCADA target list. The existing subnet-based firewall
policy permits SCADA-to-dosing Modbus and dosing-HMI-to-broker MQTT; local
PLC/analyzer/drive exchanges do not traverse a router. In GNS3, inspect
`attach-dosing-plc` or `attach-dosing-analyzer` for local Modbus,
`attach-dosing-duty-drive` and `attach-dosing-standby-drive` for cyclic I/O,
`link-plant-cell-dosing-uplink` for routed SCADA polling, and
`attach-hmi-dosing` or `link-plant-hmi-dosing-uplink` for HMI traffic. Capture
these built links with `capture_traffic` or `experiment_run`; the declaration
alone does not prove the traffic appeared in a PCAP.

### Filtration cell

`MET-PLC-FILTRATION-01` (`10.20.12.10`) polls
`MET-DP-FILTRATION-01` (`10.20.12.20`) over Modbus TCP every second. The
instrument reports differential pressure × 10 kPa and a one-second heartbeat.
Its independent sawtooth rises from 25 to 80 kPa over 120 seconds and resets;
it is a repeatable traffic stimulus, not a filter-physics model. PLC registers
0–5 report pressure × 10, phase (idle, backwashing, or waiting to rearm),
confirmed output bits (pump and valve), heartbeat, writable threshold × 10 kPa
(default 65 kPa), and alarms. Coil 0 enables automatic backwash; discrete inputs
0–2 report confirmed pump, valve, and instrument readiness. The PLC detects
missing or frozen instrument data and stops requests when it cannot trust it.

At a rising threshold crossing, the PLC requests the backwash valve, then the
pump once the valve confirms open. Both are separate local EtherNet/IP nodes:
`MET-PUMP-DRIVE-FILTRATION-BACKWASH-01` (`10.20.12.40`) and
`MET-VALVE-CTRL-FILTRATION-BACKWASH-01` (`10.20.12.42`). Their simplified I/O
uses TCP 44818 setup and UDP 2222 cyclic status/commands at 100 ms by default.
A backwash request lasts `BACKWASH_SECONDS=8`; after it ends, pressure must
fall at least `BACKWASH_HYSTERESIS_X10=100` (10 kPa) below the threshold before
another starts. The independent pressure waveform keeps increasing during a
backwash, so a commanded backwash does not itself lower the reading.

`MET-HMI-FILTRATION-01` (`10.20.52.10`) publishes an operator threshold request
only when submitted and displays SCADA status and the correlated readback
acknowledgement. Central SCADA in VLAN 20 polls this PLC every 5 seconds over
Modbus TCP; the HMI in VLAN 52 uses the broker in VLAN 23. PLC-to-instrument
Modbus and PLC-to-actuator EtherNet/IP stay local to VLAN 12. To inspect the
pattern in GNS3, capture `attach-filtration-dp` or `attach-filtration-plc` for
local Modbus, `attach-filtration-backwash-pump` and
`attach-filtration-backwash-valve` for cyclic I/O,
`link-plant-cell-filtration-uplink` for routed SCADA polling, and
`attach-hmi-filtration` or `link-plant-hmi-filtration-uplink` for HMI MQTT.
These are expected flows; packet timing and successful startup still need a
live GNS3 check.

| Expected flow | Protocol and nominal pattern | Scope and capture point |
|---|---|---|
| Filtration PLC ↔ pressure instrument | Modbus TCP read of pressure and heartbeat every 1 s; instrument value updates every 1 s | Local VLAN 12, `attach-filtration-dp` |
| Filtration PLC ↔ backwash pump and valve | TCP 44818 connection setup, then UDP 2222 commands and status every 100 ms; commands change on threshold, timeout, or fault | Local VLAN 12, both actuator attachments |
| SCADA ↔ filtration PLC | Modbus TCP read of registers 0–5 every 5 s; alarm mask and quality in MQTT snapshot | Routed VLAN 20↔12, `link-plant-cell-filtration-uplink` |
| Filtration HMI → broker → SCADA → PLC | MQTT request on operator submission, then Modbus TCP register-4 write/readback and MQTT acknowledgement | Routed VLANs 52/23/20/12, `attach-hmi-filtration` plus PLC uplink |

### Disinfection cell

`MET-PLC-DISINFECTION-01` (`10.20.13.10`) polls the networked chlorine
residual analyzer (`10.20.13.20`) over Modbus TCP. The analyzer exposes
residual × 10 mg/L and heartbeat in holding registers 0–1. The PLC exposes
residual, confirmed pump status bits, on/off output, heartbeat, writable
residual target, and alarms in holding registers 0–5. Coil 0 enables pump
requests; discrete inputs report duty pump, standby pump, and analyzer status.
The PLC requests the duty pump when its analyzer reading is below the target,
and requests the standby pump if the duty pump is unavailable, faulted, or
fails to confirm after a short grace period. Both
pumps exchange on/off cyclic EtherNet/IP I/O while connected. The analyzer's
reading is independent of pump state; there is no chlorine process model.

The PLC, analyzer, and pumps share VLAN 13. SCADA in VLAN 20 polls the PLC over
Modbus TCP and publishes status to the HMI in VLAN 53. The HMI sends a residual
target through MQTT; SCADA writes PLC register 4 and acknowledges readback.
The existing network-level firewall policy covers these routed flows. For
packet checks, use `attach-disinfection-analyzer`, both pump attachments,
`link-plant-cell-disinfection-uplink`, and `attach-hmi-disinfection` or
`link-plant-hmi-disinfection-uplink`. Startup and packet behavior still need a
live GNS3 check.

### Remote reservoir and raw-water sites

The reservoir RTU (`10.20.14.10`) polls a separate level instrument
(`10.20.14.20`) and exchanges cyclic I/O with a booster pump drive
(`10.20.14.40`). The raw-water RTU (`10.20.15.10`) follows the same pattern
with a wet-well level instrument (`10.20.15.20`) and lift-pump drive
(`10.20.15.40`). Each device is its own Docker node and has an Ethernet link
to its site switch. The instrument's Modbus TCP holding registers 0 and 1
contain level × 10 percent and a one-second heartbeat. Its value varies
independently of the drive.

Each RTU exposes six Modbus TCP holding registers: 0 level × 10 percent,
1 confirmed pump-running state, 2 instrument-ready state, 3 RTU heartbeat,
4 requested pump state, and 5 alarm bitmask. Coil 0 requests pump operation.
The RTU polls its instrument about once per second and exchanges pump commands
and status over the existing simplified EtherNet/IP model at a nominal 100 ms
interval. Central SCADA polls both RTUs over the simulated WAN about every
five seconds and publishes read-only MQTT status. The current routed path is
an untrusted underlay in the scenario; site-to-site IPsec remains future work.

| Expected flow | Capture point at reservoir | Capture point at raw water |
|---|---|---|
| RTU ↔ level instrument, local Modbus TCP | `attach-reservoir-level` | `attach-raw-water-level` |
| RTU ↔ pump drive, local cyclic EtherNet/IP | `attach-reservoir-pump` | `attach-raw-water-pump` |
| SCADA ↔ RTU, routed Modbus TCP | `link-reservoir-wan` | `link-raw-water-wan` |

These are expected flows from the declarations and models. Confirm startup,
packets, and timing in GNS3 before treating them as runtime verified.

## Routine traffic, Telnet, DNS, and time

Four nodes exist to give the lab a believable baseline:

| Node | Address | What it does |
|---|---|---|
| `MET-CONTROL-CLIENT-01` | `10.20.21.20` | Every 30 seconds: resolves the PLC, requests NTP, reads four holding registers, writes register 0 and coil 0, then reads both back |
| `MET-LEGACY-GATEWAY-01` | `10.20.10.30:23/TCP` | Telnet host with a root shell and the lab password `LabOnly_Legacy_2026` |
| `MET-DNS-01` | `10.20.30.10:53/UDP,TCP` | Resolves local names such as `plc-intake.metropolis.test` |
| `MET-NTP-01` | `10.20.30.11:123/UDP,514/UDP` | Answers NTP requests and receives lab router syslog |

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

## Telemetry produced by runnable images

The experiment collector reads each GNS3 Docker node's stdout and stderr on its
actual compute host. The following audit describes what those streams contain:

| Image or service | Container output |
|---|---|
| PLC/RTU, MQTT/CoAP sensor, SCADA, HMI, historian, control client | Application startup, sampled operation, errors; the CoAP responder emits structured audit events. |
| Mosquitto broker | Broker startup, connection and authentication notices from Mosquitto's stdout configuration. |
| Engineering workstation | Structured SSH startup/failure events; OpenSSH sends accepted and failed authentication messages to stderr, normalized as `ssh_auth_succeeded` and `ssh_auth_failed`. No passwords or keys are logged. |
| Legacy Telnet gateway | Structured startup/failure events. BusyBox `telnetd` does not reliably send individual login outcomes to container stdout/stderr; use PCAPs for Telnet session traffic. |
| DNS | `dnsmasq` query and startup output. |
| NTP and syslog relay | `chronyd -d` startup, synchronization warnings and errors; structured router syslog datagrams on UDP 514. Chrony does not emit a line for every client request by default. |
| Metropolis Open vSwitch | Structured startup/configuration events and followed `ovsdb-server`/`ovs-vswitchd` operational log files. The builder verifies each port against OVS state. |
| Mirai and Merlin synthetic nodes | Lifecycle, command and report events; the wget loader uses the same loader model. |
| Reachability probe | Intentionally idle and minimal; no service log is expected. The run manifest lists it as an intentionally silent source without counting its silence as a collector failure. |

VyOS router logs are separate from Docker stdout. Router scripts forward system,
authentication, interface and configuration messages at `info` or higher to
`MET-NTP-01` on UDP 514, where the syslog relay prints structured records for
the experiment collector. UDP can lose messages, and the collector flags routers
from which no syslog arrived. The Metropolis Open vSwitch image is a Docker node
without a routed management interface. Its supervisor forwards OVS daemon file
logs as marked JSON on Docker stdout; the run collector files these under
`logs/syslog.jsonl` with `component=switch`, `stream=syslog`, and
`transport=docker_stdout_bridge`. Unmarked stdout/stderr remains ordinary
device output. The run manifest reports missing switch events and daemon log
failures. This path requires Docker access to the GNS3 compute and does not
deliver switch UDP datagrams to `MET-NTP-01`.

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
| `MET-MERLIN-CNC-01` | `10.99.10.20` | C2 console (TCP 23) and plaintext HTTP agent channel (TCP 8443) |
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
the control conversation while the testbed stays inert. To drive the integrated
scenarios, see the experiment toolkit's `mirai_scenario` and
`merlin_scenario` tools. Merlin's scenario uses the actual inert agent node;
rebuild both Merlin images after changing their model code. To connect a
user-supplied inert Merlin agent, see the
[Merlin setup contract](../../../scripts/attacks/README.md#gotham-merlin-setup-adapter).

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
| `metropolis/plant/intake/command` | Intake HMI | SCADA, historian |
| `metropolis/scada/intake/ack` | SCADA | Intake HMI, historian |
| `metropolis/scada/dosing/status` | SCADA | Historian, dosing HMI |
| `metropolis/plant/dosing/command` | Dosing HMI | SCADA, historian |
| `metropolis/scada/dosing/ack` | SCADA | Dosing HMI, historian |
| `metropolis/scada/filtration/status` | SCADA | Historian, filtration HMI |
| `metropolis/plant/filtration/command` | Filtration HMI | SCADA, historian |
| `metropolis/scada/filtration/ack` | SCADA | Filtration HMI, historian |
| `metropolis/scada/disinfection/status` | SCADA | Historian, disinfection HMI |
| `metropolis/plant/disinfection/command` | Disinfection HMI | SCADA, historian |
| `metropolis/scada/disinfection/ack` | SCADA | Disinfection HMI, historian |
| `metropolis/scada/reservoir/status` | SCADA | Historian |
| `metropolis/scada/raw-water/status` | SCADA | Historian |

The historian subscribes to `metropolis/#` and writes what it receives to its
console for capture.

The broker rejects anonymous connections and builds its password file at
startup from the lab-only environment values. The credentials in this repository
are public test fixtures — do not reuse them outside this isolated lab. Per-role
ACLs, TLS, and per-topic permissions remain future work. The new cell status
snapshots include alarm names and a quality field, but these are lab-specific
records rather than a standard alarm protocol.

## CoAP discovery and status

With `COAP_ENABLED=true` the sensor listens on UDP 5683:

- `coap://10.20.10.20:5683/.well-known/core` returns CoRE link-format discovery
  (Content-Format 40), advertising `/status` and seven measurement resources.
- `coap://10.20.10.20:5683/status` returns JSON (Content-Format 50) with
  `device`, `sensor`, and `value`. `/` is an alias.
- `/level`, `/flow`, `/quality`, `/pressure`, `/temperature`, `/turbidity`, and
  `/pump` return text readings when the sensor has those values.

Each response writes a `COAP_AUDIT` JSON record to the container log with its
resource, response code, byte counts, peer address, and token-derived
experiment ID for joining a sender and receiver capture.

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
