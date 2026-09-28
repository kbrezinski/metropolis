# Metropolis attack-test switch

GNS3 switch role: Dedicated Layer 2 segment for authorized attack-generation nodes in the lab.

| Port role | Mode | VLAN | Network |
|---|---|---|---|
| `router-uplink` | Access/untagged | Default | `MET-OT-DMZ` eth3, `10.99.10.1/24` |
| `attack-host-1` | Access/untagged | Default | `10.99.10.0/24` |

TODO(Metropolis): Map roles to actual GNS3 ports. Router firewall isolation from OT networks is not configured yet; VLAN separation on this switch alone does not provide routed isolation.
