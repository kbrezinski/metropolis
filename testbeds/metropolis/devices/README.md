# Metropolis emulated devices

These are small, configurable protocol simulators for the GNS3 Metropolis lab, not vendor PLC firmware or safety-rated process models. Each Docker image is a reusable device model; create multiple GNS3 nodes from an image and set per-node environment variables for its identity, site, and network peers.

## Initial image set

| Model | Definition | Behavior / protocol | Suggested network |
|---|---|---|---|
| PLC / RTU | `controllers/plc/` | Modbus TCP server with a small holding-register and coil map; one image can represent either role | Plant VLANs 10-13 or remote-site LANs 14-15 |
| MQTT field sensor | `field/sensors/` | Publishes deterministic, bounded process telemetry to MQTT 3.1.1 | Plant or remote-site LAN |
| MQTT broker | `services/mqtt_broker/` | Mosquitto broker for lab telemetry and HMI commands | VLAN 23, `10.20.23.0/24` |
| SCADA collector | `operations/scada/` | Polls one Modbus TCP controller and republishes its register snapshot over MQTT | VLAN 20, `10.20.20.0/24` |
| HMI command simulator | `operations/hmi/` | Publishes a bounded setpoint command periodically over MQTT | HMI VLAN 50-53 |
| Historian collector | `operations/historian/` | Subscribes to MQTT telemetry and logs received events to container stdout | VLAN 22, `10.20.22.0/24` |
| Engineering workstation | `operations/engineering_workstation/` | Interactive Python toolbox image with Modbus and MQTT libraries | VLAN 21, `10.20.21.0/24` |

The actuator is represented by writable Modbus coils on the PLC/RTU (for example, pump run and valve open); separate actuator containers would add little at this stage. MQTT topics and the Modbus register map are documented below. Logging and time service containers remain future additions.

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
| `metropolis/mqtt-sensor:dev` | `DEVICE_NAME=MET-SENSOR-INTAKE-01`, `MQTT_HOST=10.20.23.10`, `MQTT_PORT=1883`, `MQTT_TOPIC=metropolis/plant/intake/telemetry`, `PUBLISH_INTERVAL=5`, `SENSOR_TYPE=level`, `SENSOR_VALUE=65.0` |
| `metropolis/mqtt-broker:dev` | `DEVICE_NAME=MET-MQTT-BROKER-01`; uses the checked-in Mosquitto configuration and listens on TCP 1883 |
| `metropolis/scada:dev` | `DEVICE_NAME=MET-SCADA-01`, `PLC_HOST=10.20.10.10`, `MODBUS_PORT=502`, `MODBUS_UNIT_ID=1`, `MQTT_HOST=10.20.23.10`, `MQTT_PORT=1883`, `MQTT_TOPIC=metropolis/scada/intake/status`, `POLL_INTERVAL=5` |
| `metropolis/hmi:dev` | `DEVICE_NAME=MET-HMI-INTAKE-01`, `MQTT_HOST=10.20.23.10`, `MQTT_PORT=1883`, `MQTT_TOPIC=metropolis/plant/intake/command`, `PUBLISH_INTERVAL=30`, `SETPOINT=65.0` |
| `metropolis/historian:dev` | `DEVICE_NAME=MET-HISTORIAN-01`, `MQTT_HOST=10.20.23.10`, `MQTT_PORT=1883`, `MQTT_TOPIC=metropolis/#` |
| `metropolis/engineering:dev` | Interactive shell; `docker run` defaults to a persistent shell. In GNS3, set the template start command to `/bin/sh -c 'sleep infinity'` if needed. |

Use fixed addresses from the topology plan when assigning GNS3 node addresses. The `10.20.23.10` broker address and controller address above are examples for the first lab instance; record final assignments in `testbeds/metropolis/datasets/water_treatment_v1/device_instances/` before collecting traffic.

## Modbus TCP register map

Holding registers, zero-based protocol addresses:

| Address | Meaning | Scale / example |
|---:|---|---|
| 0 | Process level | Integer engineering units × 10; default 650 = 65.0% |
| 1 | Flow rate | Integer engineering units × 10; default 120 = 12.0 units/s |
| 2 | Water quality | Integer engineering units × 10; default 950 = 95.0% |
| 3 | Device heartbeat counter | Increments once per second |

Coils 0 and 1 model a pump-run command and valve-open command. This is a basic protocol/register simulator; process values do not yet implement hydraulic cause-and-effect. TODO(Metropolis): define a realistic process model and per-cell register maps before treating generated traffic as operationally representative.

## MQTT topics

- Sensors publish JSON telemetry to a per-device topic, for example `metropolis/plant/intake/telemetry`.
- The HMI simulator publishes a JSON setpoint to `metropolis/plant/intake/command`.
- SCADA publishes its Modbus snapshot to `metropolis/scada/intake/status`.
- The historian subscribes to `metropolis/#` and writes received messages to stdout for GNS3 console/log capture.

TODO(Metropolis): Configure broker authentication, ACLs, TLS, and per-role topic permissions. The checked-in broker config is intentionally anonymous for an isolated lab demonstration and must not be exposed to untrusted networks. TODO: define realistic device-specific telemetry, command, and alarm payload schemas.
