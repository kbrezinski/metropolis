# Metropolis emulated devices

These are small, configurable protocol simulators for the GNS3 Metropolis lab, not vendor PLC firmware or safety-rated process models. Each Docker image is a reusable device model; create multiple GNS3 nodes from an image and set per-node environment variables for its identity, site, and network peers.

## Initial image set

| Model | Definition | Behavior / protocol | Suggested network |
|---|---|---|---|
| PLC / RTU | `controllers/plc/` | Modbus TCP server with a small holding-register and coil map; one image can represent either role | Plant VLANs 10-13 or remote-site LANs 14-15 |
| MQTT/CoAP field sensor | `field/sensors/` | Publishes deterministic MQTT telemetry; exposes CoAP discovery and JSON status over UDP 5683 | Plant or remote-site LAN |
| MQTT broker | `services/mqtt_broker/` | Authenticated Mosquitto broker for lab telemetry and HMI commands | VLAN 23, `10.20.23.0/24` |
| SCADA collector | `operations/scada/` | Polls one Modbus TCP controller and republishes its register snapshot over MQTT | VLAN 20, `10.20.20.0/24` |
| HMI operator panel | `operations/hmi/` | Authenticated HTTP panel, MQTT telemetry subscription, and bounded setpoint publisher | HMI VLAN 50-53 |
| Historian collector | `operations/historian/` | Subscribes to MQTT telemetry and logs received events to container stdout | VLAN 22, `10.20.22.0/24` |
| Engineering workstation | `operations/engineering_workstation/` | OpenSSH shell and SFTP, with Python Modbus and MQTT libraries | VLAN 21, `10.20.21.0/24` |
| Routine control client | `operations/control_client/` | Resolves the PLC by DNS, samples NTP, and periodically reads/writes Modbus registers and coils | VLAN 21, `10.20.21.0/24` |
| Legacy gateway | `field/legacy_gateway/` | Lab Telnet login and shell for experiments involving legacy host access | Intake VLAN 10 |
| DNS | `services/dns/` | Local records for the Metropolis v1 lab; no upstream forwarding | OT services VLAN 30 |
| Time service | `services/ntp/` | Local NTP server on UDP 123, using the host clock without changing it | OT services VLAN 30 |

The actuator is represented by writable Modbus coils on the PLC/RTU (for example, pump run and valve open); separate actuator containers would add little at this stage. MQTT topics and the Modbus register map are documented below. A central log collector remains future work.

## Build the images

Run these from the repository root. Docker uses the repository root as the build context so the Dockerfiles can copy the shared model code.

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
```


In GNS3, create Docker templates using these images, then configure each node's environment variables. Do not configure Docker port publishing for nodes connected directly to GNS3 links; processes bind to the node's own interfaces.

## GNS3 node network setup

Every image uses `testbeds/metropolis/devices/runtime/network-entrypoint.sh` as its container entrypoint. Before starting the application it:

1. Sets the Linux hostname when `NODE_HOSTNAME` is provided.
2. Waits up to 30 seconds for `NODE_INTERFACE` (defaults to `eth0`) to exist when `NODE_IP` is provided.
3. Brings that interface up and applies `NODE_IP` as an IPv4 CIDR address.
4. Replaces the default IPv4 route through `NODE_GATEWAY` when one is provided.

Set these environment values on each GNS3 Docker node/template. Example for the intake PLC:

```text
NODE_HOSTNAME=MET-PLC-INTAKE-01
NODE_IP=10.20.10.10/24
NODE_GATEWAY=10.20.10.1
NODE_INTERFACE=eth0
```

The matching values for the proposed nodes are included in `testbeds/metropolis/datasets/water_treatment_v1/device_instances/initial_devices.yaml`; copy them into the GNS3 environment settings. GNS3 supports environment variables in Docker templates and node configuration, and its Docker node configuration grants the networking capabilities used by this setup. The interface must be connected to a GNS3 link before the node starts. For nodes with multiple NICs, set `NODE_INTERFACE` to the interface on the intended subnet; the current models support one configured IPv4 interface and one default gateway per node.

If `NODE_IP` is empty, IP setup is skipped. If `NODE_GATEWAY` is set without `NODE_IP`, startup stops with an error. These images are intended to behave as GNS3 nodes; running them as ordinary Docker services with custom IP settings also requires the container to have the `NET_ADMIN` and `SYS_ADMIN` capabilities.

## Defaults and environment variables

| Image | Environment variables |
|---|---|
| All images | `NODE_HOSTNAME`, `NODE_IP` (IPv4 CIDR), `NODE_GATEWAY` (optional), `NODE_INTERFACE` (defaults to `eth0`) |
| `metropolis/controller:dev` | `DEVICE_NAME=MET-PLC-INTAKE-01`, `DEVICE_ROLE=PLC`, `MODBUS_HOST=0.0.0.0`, `MODBUS_PORT=502`, `PROCESS_LEVEL=650`, `PROCESS_FLOW=120`, `PROCESS_QUALITY=950` |
| `metropolis/mqtt-sensor:dev` | `DEVICE_NAME=MET-SENSOR-INTAKE-01`, `MQTT_USERNAME=lab_device`, `MQTT_PASSWORD=LabOnly_Device_2026`, `MQTT_HOST=10.20.23.10`, `MQTT_PORT=1883`, `MQTT_TOPIC=metropolis/plant/intake/telemetry`, `COAP_ENABLED=true`, `COAP_PORT=5683` |
| `metropolis/mqtt-broker:dev` | `DEVICE_NAME=MET-MQTT-BROKER-01`; requires a generated password file and listens on TCP 1883. Synthetic users: `lab_device` / `LabOnly_Device_2026`, `lab_operator` / `LabOnly_MQTT_2026`. |
| `metropolis/scada:dev` | `DEVICE_NAME=MET-SCADA-01`, `PLC_HOST=10.20.10.10`, `MODBUS_PORT=502`, `MODBUS_UNIT_ID=1`, `MQTT_USERNAME=lab_device`, `MQTT_PASSWORD=LabOnly_Device_2026`, `MQTT_HOST=10.20.23.10`, `MQTT_PORT=1883`, `MQTT_TOPIC=metropolis/scada/intake/status`, `POLL_INTERVAL=5` |
| `metropolis/hmi:dev` | `DEVICE_NAME=MET-HMI-INTAKE-01`, `MQTT_USERNAME=lab_device`, `MQTT_PASSWORD=LabOnly_Device_2026`, `MQTT_HOST=10.20.23.10`, `MQTT_PORT=1883`, `MQTT_TOPIC=metropolis/plant/intake/command`, `PUBLISH_INTERVAL=30`, `SETPOINT=65.0` |
| `metropolis/historian:dev` | `DEVICE_NAME=MET-HISTORIAN-01`, `MQTT_USERNAME=lab_device`, `MQTT_PASSWORD=LabOnly_Device_2026`, `MQTT_HOST=10.20.23.10`, `MQTT_PORT=1883`, `MQTT_TOPIC=metropolis/#` |
| `metropolis/engineering:dev` | Required `ENGINEER_PASSWORD`; runs OpenSSH on TCP 22 with the fixed non-root account `engineer`. |
| `metropolis/control-client:dev` | `PLC_DNS_NAME=plc-intake.metropolis.test`, `DNS_SERVER=10.20.30.10`, `NTP_SERVER=10.20.30.11`, `MODBUS_PORT=502`, `MODBUS_UNIT_ID=1`, `CONTROL_INTERVAL=30` |
| `metropolis/legacy-gateway:dev` | Requires `LEGACY_ROOT_PASSWORD`; runs BusyBox Telnet on TCP 23 with a lab-only root login. |
| `metropolis/dns:dev` | Local DNS records on UDP/TCP 53; edit `services/dns/dnsmasq.conf` to change this dataset's names. |
| `metropolis/ntp:dev` | Local NTP on UDP 123; serves an isolated local reference. |

Use fixed addresses from the topology plan when assigning GNS3 node addresses. The `10.20.23.10` broker address and controller address above are examples for the first lab instance; record final assignments in `testbeds/metropolis/datasets/water_treatment_v1/device_instances/` before collecting traffic.

## HMI web and engineering host services

The initial inventory configures these normal services:

| Node | Endpoint | Public lab username / password |
|---|---|---|
| `MET-HMI-INTAKE-01` | `http://10.20.50.10:8080/` | `operator` / `LabOnly_HMI_2026` |
| `MET-ENGINEERING-01` | SSH and SFTP at `10.20.21.10:22` | `engineer` / `LabOnly_Engineering_2026` |

Rebuild the HMI and engineering images using the commands above, then update
or recreate their GNS3 nodes. Copy their environment settings from
[the inventory](../datasets/water_treatment_v1/device_instances/initial_devices.yaml).
Clear any old engineering template start-command override such as `sleep infinity`
so the image's default SSH startup runs. Attach the engineering node to VLAN 21
and the HMI to VLAN 50 using the existing switch specifications.

The HMI requires `HMI_PASSWORD`. `HMI_USERNAME` defaults to `operator`,
`HMI_HTTP_HOST` to `0.0.0.0`, and `HMI_HTTP_PORT` to `8080`.
`MQTT_TELEMETRY_TOPIC` defaults to `metropolis/scada/intake/status`.
The browser panel displays the latest received SCADA payload and its timestamp,
and accepts numeric level setpoints from 0 to 100 percent. Commands are published
on the existing MQTT command topic periodically and after an operator change.
There is still no command consumer that applies these requests to a PLC.

All HTTP routes require Basic authentication: `/`, `/api/status`, `/healthz`,
and `POST /api/setpoint`. The POST body is `{"value":65}` with
`Content-Type: application/json` and `X-Metropolis-Request: hmi` headers.
HTTP 202 means accepted locally, not broker delivery or PLC acknowledgement.
`last_publish_queued_at` records local MQTT queue acceptance only. `/healthz`
reports HTTP availability and the separate MQTT connection flag; telemetry can
be stale even while MQTT is connected. State is in memory and resets on restart.
The small Python HTTP server is a lab model; HTTP and Basic credentials are
unencrypted. These published credentials are synthetic experiment fixtures.

Engineering uses real OpenSSH with a non-root shell and SFTP for maintenance
files. Root login and SSH forwarding features are disabled, and the account has
no sudo access. This is a real shell, not a restricted command emulator.
Host keys are generated at first startup in `/var/lib/metropolis/ssh`; persist
that directory per node if host identity should survive container replacement.
Never share a host-key volume between nodes. Passwords remain visible in the
lab's node configuration. The separate legacy gateway now provides Telnet;
malware and botnet infrastructure are absent.

From a host with a route to these lab subnets, check normal use:

```bash
curl -u operator http://10.20.50.10:8080/api/status
ssh engineer@10.20.21.10
sftp engineer@10.20.21.10
```

Enter the corresponding lab password when prompted. Visit the HMI in a browser,
submit a setpoint, and check the historian console for the command topic.
These generate ordinary HTTP, MQTT, SSH, and SFTP capture traffic. Local HTTP
tests exercise authentication and validation; Linux image startup, SSH login,
and the full GNS3/MQTT path still require deployment verification.

## Modbus TCP register map

Holding registers, zero-based protocol addresses:

| Address | Meaning | Scale / example |
|---:|---|---|
| 0 | Process level | Integer engineering units × 10; default 650 = 65.0% |
| 1 | Flow rate | Integer engineering units × 10; default 120 = 12.0 units/s |
| 2 | Water quality | Integer engineering units × 10; default 950 = 95.0% |
| 3 | Device heartbeat counter | Increments once per second |

Coils 0 and 1 model a pump-run command and valve-open command. This is a basic protocol/register simulator; process values do not yet implement hydraulic cause-and-effect. TODO(Metropolis): define a realistic process model and per-cell register maps before treating generated traffic as operationally representative.

## Routine control, Telnet, DNS, and time

The v1 inventory adds four GNS3 nodes:

| Node | Address | Normal role |
|---|---|---|
| `MET-CONTROL-CLIENT-01` | `10.20.21.20` | Every 30 seconds, resolve the intake PLC, request NTP time, read four holding registers, write holding register 0 and coil 0, then read back both writes |
| `MET-LEGACY-GATEWAY-01` | `10.20.10.30:23/TCP` | Separate legacy Telnet host with a root shell and the synthetic lab password `LabOnly_Legacy_2026` |
| `MET-DNS-01` | `10.20.30.10:53/UDP,TCP` | Resolve local names such as `plc-intake.metropolis.test` |
| `MET-NTP-01` | `10.20.30.11:123/UDP` | Respond to NTP client requests |

Start DNS and NTP before the control client. The client uses explicit DNS and
NTP server addresses in its environment, so it does not require a DHCP or
container resolver change. Its level writes follow the repeating sequence
650, 652, 654, 656, 658 (representing 65.0–65.8%). Coil 0 is true for six
cycles, then false for two. These are regular, bounded baseline commands; the
PLC's process variables do not react to the pump coil. Failed cycles log to
stdout and retry. The NTP reply is observed, not used to adjust the clock.

The legacy gateway exists to expose a real Telnet login and shell in a
controlled experiment network. Credentials travel in clear text. Use the
synthetic password only within this isolated GNS3 testbed, and do not bridge
this node to a production network. A Gotham Mirai experiment still needs its
other lifecycle nodes and scripts adapted separately. No attack runner or
botnet component is included here.

After building the images and configuring the nodes from the inventory, verify
normal service behavior from a routed lab host:

```bash
dig @10.20.30.10 plc-intake.metropolis.test
chronyd -Q -t 5 'server 10.20.30.11 iburst'
telnet 10.20.10.30
```

For Telnet, log in as `root` with the inventory's lab password. Confirm that
the control client logs successful cycles and that SCADA observes register 0
changes. Record capture points on the control-client and intake links. These
service models have not yet been built or exercised in a live GNS3 deployment.

## MQTT topics

- Sensors publish JSON telemetry to a per-device topic, for example `metropolis/plant/intake/telemetry`.
- The HMI simulator publishes a JSON setpoint to `metropolis/plant/intake/command`.
- SCADA publishes its Modbus snapshot to `metropolis/scada/intake/status`.
- The historian subscribes to `metropolis/#` and writes received messages to stdout for GNS3 console/log capture.

The broker rejects anonymous MQTT connections. Its entrypoint creates the password file at startup from the synthetic lab-only environment values. The checked-in usernames and passwords are public test credentials; do not reuse them outside this isolated lab. MQTT ACLs, TLS, and per-role topic permissions remain future work. The CoAP status resource is hosted by the existing MQTT sensor node and can support future protocol experiments or traffic generation. TODO: define realistic device-specific telemetry, command, and alarm payload schemas.

## CoAP discovery and status

With `COAP_ENABLED=true`, the sensor listens on UDP 5683. For the initial
intake sensor, normal client targets are:

- `coap://10.20.10.20:5683/.well-known/core`: CoRE link-format discovery
  (Content-Format 40), advertising `</status>;rt="metropolis.sensor";ct=50`.
- `coap://10.20.10.20:5683/status`: JSON (Content-Format 50) containing
  `device`, `sensor`, and `value`. `/` remains an alias for compatibility.

Rebuild `metropolis/mqtt-sensor:dev` and recreate/update the GNS3 sensor node
to use changes to this service. Unknown URIs return 4.04, unsupported methods
4.05, and incompatible Accept formats 4.06. Malformed packets are ignored.
This small server supports CON/NON GET requests; it does not implement DTLS,
Observe, blockwise transfer, discovery query filtering, or a duplicate-request
cache. It is not a complete CoAP stack. See the
[guide organized by attack type](../../../scripts/attacks/README.md) for service
targets, experiment prerequisites, and how to distinguish response-size
measurements from a reflection scenario.
