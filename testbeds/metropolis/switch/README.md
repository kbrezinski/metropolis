# Metropolis switch reference

**Generated file. Do not edit by hand.**

Written by `scripts/generate_switch_reference.py` using
`metropolis.topology`, from `device_instances/infrastructure.yaml` and
`topology/links.yaml`, so it cannot disagree with the topology the builder
creates. To change it, change the data and regenerate.

Each switch uses the Metropolis Open vSwitch Docker image. Port numbers come from the
order of `attachment_points` in the infrastructure inventory, so the port
numbering below is exactly what the build uses.

## `sw-plant-aggregation`

Layer 2 aggregation between MET-PLANT and the plant and HMI access switches.

| Port | Role | Mode | VLANs | Far end |
|---|---|---|---|---|
| 0 | `router-uplink` | trunk | 10, 11, 12, 13, 50, 51, 52, 53 | `MET-PLANT` eth1 — Plant router trunk to the plant aggregation switch |
| 1 | `cell-intake-uplink` | trunk | 10 | `sw-plant-cell-intake` (aggregation-uplink) — Plant aggregation to the intake cell access switch |
| 2 | `cell-dosing-uplink` | trunk | 11 | `sw-plant-cell-dosing` (aggregation-uplink) — Plant aggregation to the dosing cell access switch |
| 3 | `cell-filtration-uplink` | trunk | 12 | `sw-plant-cell-filtration` (aggregation-uplink) — Plant aggregation to the filtration cell access switch |
| 4 | `cell-disinfection-uplink` | trunk | 13 | `sw-plant-cell-disinfection` (aggregation-uplink) — Plant aggregation to the disinfection cell access switch |
| 5 | `hmi-intake-uplink` | trunk | 50 | `sw-plant-hmi` (aggregation-uplink) — Plant aggregation to the intake HMI access switch |
| 6 | `hmi-dosing-uplink` | trunk | 51 | `sw-plant-dosing-hmi` (aggregation-uplink) — Plant aggregation to the dosing HMI access switch |
| 7 | `hmi-filtration-uplink` | trunk | 52 | `sw-plant-filtration-hmi` (aggregation-uplink) — Plant aggregation to the filtration HMI access switch |
| 8 | `hmi-disinfection-uplink` | trunk | 53 | `sw-plant-disinfection-hmi` (aggregation-uplink) — Plant aggregation to the disinfection HMI access switch |

## `sw-operations-aggregation`

Layer 2 aggregation between MET-OT-CORE and the OT service access switches.

| Port | Role | Mode | VLANs | Far end |
|---|---|---|---|---|
| 0 | `router-uplink` | trunk | 20, 21, 22, 23, 30 | `MET-OT-CORE` eth3 — OT core trunk to the operations aggregation switch |
| 1 | `scada-uplink` | trunk | 20 | `sw-operations-scada` (aggregation-uplink) — Operations aggregation to the SCADA access switch |
| 2 | `engineering-uplink` | trunk | 21 | `sw-operations-engineering` (aggregation-uplink) — Operations aggregation to the engineering access switch |
| 3 | `historian-uplink` | trunk | 22 | `sw-operations-historian` (aggregation-uplink) — Operations aggregation to the historian access switch |
| 4 | `mqtt-uplink` | trunk | 23 | `sw-operations-mqtt` (aggregation-uplink) — Operations aggregation to the MQTT access switch |
| 5 | `ot-services-uplink` | trunk | 30 | `sw-operations-services` (aggregation-uplink) — Operations aggregation to the OT services access switch |

## `sw-wan-underlay`

Shared Layer 2 underlay for the WAN hub and remote-site routers.

| Port | Role | Mode | VLANs | Far end |
|---|---|---|---|---|
| 0 | `wan-hub` | access | untagged | `MET-OT-WAN` eth1 — WAN hub router to the simulated WAN switch |
| 1 | `raw-water-site` | access | untagged | `MET-RAW-WATER` eth1 — Raw-water router to the simulated WAN switch |
| 2 | `reservoir-site` | access | untagged | `MET-RESERVOIR` eth1 — Reservoir router to the simulated WAN switch |

## `sw-dmz`

Layer 2 segment for OT DMZ hosts.

| Port | Role | Mode | VLANs | Far end |
|---|---|---|---|---|
| 0 | `router-uplink` | access | untagged | `MET-OT-DMZ` eth1 — OT/DMZ router to the DMZ switch |
| 1 | `dmz-host-1` | spare | — | nothing cabled |
| 2 | `dmz-host-2` | spare | — | nothing cabled |

## `sw-enterprise`

Layer 2 enterprise-side segment attached to the boundary router.

| Port | Role | Mode | VLANs | Far end |
|---|---|---|---|---|
| 0 | `router-uplink` | access | untagged | `MET-OT-DMZ` eth2 — OT/DMZ router to the enterprise switch |
| 1 | `enterprise-host-1` | spare | — | nothing cabled |

## `sw-attack-test`

Dedicated Layer 2 segment for attack-generation nodes.

| Port | Role | Mode | VLANs | Far end |
|---|---|---|---|---|
| 0 | `router-uplink` | access | untagged | `MET-OT-DMZ` eth3 — OT/DMZ router to the isolated attack-test switch |
| 1 | `attack-host-1` | access | untagged | `MET-MIRAI-CNC-01` — Synthetic Mirai control node on the attack-test segment |
| 2 | `attack-host-2` | access | untagged | `MET-MERLIN-CNC-01` — Merlin control node on the attack-test segment |
| 3 | `attack-host-3` | access | untagged | `MET-MERLIN-AGENT-01` — Merlin agent on the attack-test segment |

## `sw-reservoir-access`

Access Layer 2 segment for the reservoir/booster station.

| Port | Role | Mode | VLANs | Far end |
|---|---|---|---|---|
| 0 | `router-uplink` | access | untagged | `MET-RESERVOIR` eth0 — Reservoir router to the reservoir station switch |
| 1 | `rtu-or-plc` | spare | — | nothing cabled |
| 2 | `sensor-actuator-1` | spare | — | nothing cabled |
| 3 | `sensor-actuator-2` | spare | — | nothing cabled |

## `sw-raw-water-access`

Access Layer 2 segment for the raw-water lift station.

| Port | Role | Mode | VLANs | Far end |
|---|---|---|---|---|
| 0 | `router-uplink` | access | untagged | `MET-RAW-WATER` eth0 — Raw-water router to the raw-water station switch |
| 1 | `rtu-or-plc` | spare | — | nothing cabled |
| 2 | `sensor-actuator-1` | spare | — | nothing cabled |
| 3 | `sensor-actuator-2` | spare | — | nothing cabled |

## Notes

- A port marked *spare* has no cable in `links.yaml`. It exists in the switch
  specification as room to extend, and nothing is attached to it.
- Trunk links carry the listed VLANs tagged; access links carry one untagged
  VLAN.
- VLAN numbers and subnets come from `topology/address-plan.yaml`. The router
  trunks must carry the same VLANs listed here, and
  `scripts/validate_metropolis_topology.py` checks that they do.
- Firewall policy and IPsec are router-layer concerns and are not configured.
  Switch VLAN membership separates traffic; it does not filter it.
- This table is the declared intent. The Metropolis Open vSwitch Docker image
  receives these settings from the topology builder, which checks applied OVS
  state and reports any switch that remains unconfigured.
