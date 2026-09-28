# Metropolis reservoir/booster station access switch

GNS3 switch role: Access Layer 2 segment for the reservoir/booster station.

| Port role | Mode | VLAN | Network |
|---|---|---|---|
| `router-uplink` | Access/untagged | Default | `MET-RESERVOIR` eth0, `10.20.14.1/24` |
| `rtu-or-plc` | Access/untagged | Default | `10.20.14.0/24` |
| `sensor-actuator-1` | Access/untagged | Default | `10.20.14.0/24` |
| `sensor-actuator-2` | Access/untagged | Default | `10.20.14.0/24` |

TODO(Metropolis): Map roles to actual GNS3 ports and assign device addresses. IPsec and site firewall configuration remain pending on the router.
