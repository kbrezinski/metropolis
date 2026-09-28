# Metropolis OT DMZ switch

GNS3 switch role: Layer 2 segment for OT DMZ hosts.

| Port role | Mode | VLAN | Network |
|---|---|---|---|
| `router-uplink` | Access/untagged | Default | `MET-OT-DMZ` eth1, `10.20.40.1/24` |
| `dmz-host-1` | Access/untagged | Default | `10.20.40.0/24` |
| `dmz-host-2` | Access/untagged | Default | `10.20.40.0/24` |

TODO(Metropolis): Map roles to actual GNS3 ports. Add VLAN tagging only if this segment is later redesigned as a trunk.
