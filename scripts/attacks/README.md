# Gotham experiments: Metropolis service targets by attack type

Metropolis supplies the network and normal device services. Researchers bring
their experiment tools from [Gotham](https://github.com/xsaga/gotham-iot-testbed)
or another implementation. This directory contains guidance only.

**“Implemented” below means a device model/configuration exists in this
repository. It does not mean a running GNS3 deployment has been verified.**
Build/start the referenced nodes and confirm normal service responses first.

## Choose an experiment

| Attack type | Where to point the external tool | Service readiness |
|---|---|---|
| MQTT credential attempts | `10.20.23.10:1883/TCP` | Authenticated Mosquitto implemented |
| MQTT publish/subscribe misuse | Same broker; topics listed below | Broker, publishers, and historian implemented; physical process effects absent |
| Network/service scanning | MQTT `1883/TCP`, PLC/RTUs `502/TCP`, sensor `5683/UDP`, Telnet `23/TCP`, DNS `53/TCP,UDP`, NTP `123/UDP` | Listeners specified in device images; ranges come from the address plan |
| Availability / DoS experiments | Select an existing listener below | Normal target services exist; load generation and impact measurement are external |
| CoAP reflection / response-size experiments | `coap://10.20.10.20:5683/.well-known/core` | Discovery and status implemented; experiment victim and generator are external |
| Mirai / other botnet lifecycle | `10.20.10.30:23/TCP` is a Telnet-capable legacy host | Lab shell target added; Gotham loader, reporting, C2, and attack orchestration absent |
| Modbus manipulation (OT extension) | `10.20.10.10:502/TCP` or remote RTUs | Register/coil model and routine Modbus writes implemented; no physical water-process model |
| HTTP authentication / application experiments | `10.20.50.10:8080/TCP` | HMI Basic authentication and setpoint API implemented |
| SSH authentication / host-access experiments | `10.20.21.10:22/TCP` | Real OpenSSH shell and SFTP implemented |

## 1. MQTT credential attempts

**Target:** `MET-MQTT-BROKER-01`, `10.20.23.10:1883/TCP`.
Use this mapping when adapting Gotham's MQTT Metasploit workflow.

| Public lab account | Password | Normal use |
|---|---|---|
| `lab_device` | `LabOnly_Device_2026` | Sensor, SCADA, HMI, and historian clients |
| `lab_operator` | `LabOnly_MQTT_2026` | Research tools |

Mosquitto rejects anonymous connections. The broker's startup script generates
its password file from `MQTT_DEVICE_*` and `MQTT_OPERATOR_*` environment values.
No TLS listener or per-topic ACL is configured. Both accounts currently have
the same broker permissions; their names do not establish authorization roles.

**Change in your Gotham copy:** MQTT destination, port, account, credential
fixture path, GNS3 project, and the exact Metasploit node name. Tool installation
and credential fixtures belong to the researcher; there is no bundled attacker
node. Verify a known-valid and a known-invalid connection before a run and
retain the broker's stdout alongside the PCAP. Check actual module completion
rather than assuming a fixed console delay proves success.

## 2. MQTT publish/subscribe misuse

**Target:** the same broker, with these normal application topics:

| Topic | Producer | Consumer/observable effect |
|---|---|---|
| `metropolis/plant/intake/telemetry` | `MET-SENSOR-INTAKE-01` | Historian stdout |
| `metropolis/scada/intake/status` | `MET-SCADA-01` | Historian stdout |
| `metropolis/plant/intake/command` | `MET-HMI-INTAKE-01` | Historian stdout; no controller command handler |

`MET-HISTORIAN-01` subscribes to `metropolis/#` at the broker. It is an MQTT
client at `10.20.22.10`, not a second broker. Likewise, the sensor and HMI do
not listen for MQTT connections. Point MQTT tools at `10.20.23.10`.

**Change in your Gotham copy:** broker, authentication, topic names, payload
schema, and client IDs. Baseline telemetry is JSON. MQTT publications can be
observed at the broker/historian, but the HMI command topic is not connected to
PLC actuation. Claiming physical manipulation would require a separate process
model and command consumer. Broker authentication alone does not test topic ACLs.

## 3. Network and service scanning

The following listeners are specified by the current inventory:

| Node | Address | Expected listener |
|---|---|---|
| `MET-MQTT-BROKER-01` | `10.20.23.10` | TCP 1883 |
| `MET-PLC-INTAKE-01` | `10.20.10.10` | TCP 502 |
| `MET-RTU-RESERVOIR-01` | `10.20.14.10` | TCP 502 |
| `MET-RTU-RAW-WATER-01` | `10.20.15.10` | TCP 502 |
| `MET-SENSOR-INTAKE-01` | `10.20.10.20` | UDP 5683 |
| `MET-HMI-INTAKE-01` | `10.20.50.10` | TCP 8080 (HTTP) |
| `MET-ENGINEERING-01` | `10.20.21.10` | TCP 22 (SSH/SFTP) |
| `MET-LEGACY-GATEWAY-01` | `10.20.10.30` | TCP 23 (Telnet) |
| `MET-DNS-01` | `10.20.30.10` | UDP/TCP 53 (DNS) |
| `MET-NTP-01` | `10.20.30.11` | UDP 123 (NTP) |

**Change in your Gotham copy:** target ranges, selected transport/ports, source
interface, and device-name assumptions. A TCP check cannot establish whether
the UDP CoAP service is available. Do not assume every inventoried application
node exposes a listener: SCADA and historian primarily initiate connections.
DNS and RTSP services are not part of these images.

## 4. Availability and DoS experiments

Normal listeners above provide targets for externally supplied traffic tools,
including adaptations of Gotham's `hping3_attacks.sh`. A special “DoS server”
is not required to measure the availability of an existing service.

**Change in your Gotham copy:** destination, transport, port, experiment
duration, and traffic parameters. TCP 1883 is an MQTT listener; sending UDP to
1883 would measure different behavior and would not exercise MQTT processing.

**Measure separately:** successful service transactions and latency before,
during, and after a run. Existing SCADA polls (default five seconds), MQTT
telemetry, broker logs, and historian output provide baseline observations.
There is no dedicated health-monitor node or automated loss/latency analysis.
Packet volume alone is not evidence that service availability was affected.

## 5. CoAP reflection and response-size experiments

**Service node:** `MET-SENSOR-INTAKE-01`, `10.20.10.20:5683/UDP`.
Set `COAP_ENABLED=true` (the default). Rebuild the sensor image after code changes.

| URI | Normal GET response |
|---|---|
| `coap://10.20.10.20:5683/.well-known/core` | `2.05 Content`, `application/link-format` (40); advertises `/status` |
| `coap://10.20.10.20:5683/status` | `2.05 Content`, `application/json` (50); device, sensor type, and value |
| `coap://10.20.10.20:5683/` | Backward-compatible alias for status |

Discovery follows the CoRE link format described in
[RFC 6690](https://www.rfc-editor.org/rfc/rfc6690.txt). The server advertises
only its implemented status resource; it does not pad responses to obtain a
particular response-size ratio. Unknown paths return `4.04`, unsupported
methods `4.05`, and incompatible Accept formats `4.06`.

There is no server-side “enable amplification” setting. Response expansion is
a measured relationship between request and response sizes. Reflection also
requires an experiment source, a separate victim/receiver, and a network path
that permits the relevant traffic; having a CoAP endpoint alone proves none
of those conditions. This repository supplies the normal responder. Authors
supply their experiment source/victim and document the routing/filtering state.

**Change in your Gotham copy:** CoAP service address, port, resource URI, and
the separately defined experiment roles. A script's `target` variable may mean
the victim rather than the responder: inspect how that variable is used. A
direct GET is the service preflight, not proof of a reflection experiment.
Measure response/request sizes from captures at the same protocol layer;
Gotham's reported payloads and ratios cannot be assumed for this resource list.

This remains a small CoAP implementation: no DTLS, Observe, blockwise transfer,
discovery query filtering, or duplicate-request cache. Unsupported critical
options are rejected. Protocol fidelity beyond these GET resources would call
for a fuller CoAP stack. No live reflection scenario has been validated here.

## 6. Mirai and other botnet lifecycle experiments

`MET-LEGACY-GATEWAY-01` at `10.20.10.30:23/TCP` provides a real Telnet login
and container root shell with the synthetic `root` /
`LabOnly_Legacy_2026` lab account. This supports Telnet service discovery and
login traces. Mirai bots, loader/reporting services, and C2 services are not
included. A normal MQTT broker is not a replacement for Mirai's C2.

The Telnet node supplies one victim role, not the complete lifecycle.
Changing IP addresses in `run_mirai.py` is insufficient; the script's GNS3
node names, credential list, binary architecture, loader, reporting, C2, and
network paths still need review and deployment. No Mirai implementation is
part of the current repository. The synthetic password is deliberately not
claimed to match Gotham's existing credential wordlist.

## 7. Modbus manipulation: a Metropolis OT extension

**Targets:** the PLC/RTUs on TCP 502 in section 3. SCADA normally reads the
intake PLC's four holding registers: level, flow, quality, and heartbeat.
Coils represent basic pump/valve state; see the
[device register map](../../testbeds/metropolis/devices/README.md#modbus-tcp-register-map).
`MET-CONTROL-CLIENT-01` at `10.20.21.20` now performs a regular read/write
cycle on the intake PLC after DNS lookup and NTP sampling. This gives captures
a normal Modbus-write baseline and independent DNS/NTP background traffic.

This is an OT extension rather than a direct substitution for Gotham's MQTT
or CoAP scripts. External tools must use the correct Modbus unit ID, function,
and datastore addresses. The process values are synthetic; changing a register
or coil is not a validated model of damage to a water-treatment process.

## 8. HTTP authentication and application experiments

Target `MET-HMI-INTAKE-01` at `http://10.20.50.10:8080/`.
The public lab account is `operator` / `LabOnly_HMI_2026`.
The panel, status API, and setpoint API require HTTP Basic authentication.
See the [host-service guide](../../testbeds/metropolis/devices/README.md#hmi-web-and-engineering-host-services)
for normal requests, required headers, payload validation, and response semantics.
External tools must support HTTP Basic authentication; an MQTT login module
does not exercise this endpoint. No vendor HMI exploit compatibility is claimed.
Capture normal browser polling and setpoint requests before an experiment.

## 9. SSH authentication and host access

Target `MET-ENGINEERING-01` at `10.20.21.10:22/TCP`.
The public lab account is `engineer` / `LabOnly_Engineering_2026`.
OpenSSH provides a real non-root shell and SFTP; logs go to container stderr.
Researchers must select SSH-capable tools and the correct account rather than
pointing a Telnet or MQTT module at this service. Root login is disabled.
The SSH host remains separate from the Telnet gateway in section 6.

## Shared setup, capture, and services worth adding

The authoritative configuration is the
[device inventory](../../testbeds/metropolis/datasets/water_treatment_v1/device_instances/initial_devices.yaml)
and [address plan](../../testbeds/metropolis/datasets/water_treatment_v1/topology/address-plan.yaml).
The tables above summarize that version. Future testbeds can use different
addresses while retaining the same service roles and external experiment logic.

Replace Gotham's `gotham_scenario` with your actual GNS3 project name (the
suggested Metropolis name is `metropolis_water_treatment_v1`). Replace
`iotsim-*` searches and exact node lookups with your explicit node mapping.
Gotham's GNS3 helpers, Docker/container lookup, installed tools, console state,
and container filesystem paths also need to match your deployment.

Authors can attach an experiment host to the planned attack-test network
`10.99.10.0/24`, gateway `10.99.10.1`, using the switch's `attack-host-1` role.
That host is not provisioned by this repository. Router firewall policy is
still a TODO; the static routes describe reachability, not containment.

Capture on actual GNS3 links connected to the experiment source and relevant
service/victim. Traffic between hosts on one subnet may never cross a router;
a backbone-only capture can miss it. Save UTC start/end times, node/image and
tool versions, addressing, service configuration, capture points, and normal
baseline traffic with each dataset. Synchronize capture hosts; ordinary Docker
containers share their host's clock. The legacy Gotham CSV labeling utility
does not establish labels for new Metropolis captures.

These shared services would make future experiments easier, but are **planned,
not implemented or assigned target addresses here**:

| Addition | Benefit |
|---|---|
| Health-monitor and log collector | Consistent service latency, failure, and recovery evidence |
| Dedicated receiver/victim node | Stable endpoint for experiments that require a separate receiving host |
| Optional RTSP device models | Additional application targets when a chosen Gotham scenario requires them |

For Gotham methodology and attribution, see the
[Gotham paper](https://doi.org/10.1109/TDSC.2023.3247166).
