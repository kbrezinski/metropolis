# Metropolis OT operations aggregation switch

GNS3 switch role: Layer 2 aggregation between `MET-OT-CORE` and OT operations/service access switches.

| Port role | Mode | VLANs | Notes |
|---|---|---|---|
| `router-uplink` | Trunk | 20-23, 30 | Connect to `MET-OT-CORE` eth3 |
| `scada-uplink` | Trunk | 20 | SCADA access switch |
| `engineering-uplink` | Trunk | 21 | Engineering workstation access switch |
| `historian-uplink` | Trunk | 22 | Historian access switch |
| `mqtt-uplink` | Trunk | 23 | MQTT broker access switch |
| `ot-services-uplink` | Trunk | 30 | Logging, time, and infrastructure services access switch |

TODO(Metropolis): Map each role to the actual GNS3 port number. TODO: Confirm the appliance supports the listed trunk VLAN behavior.
