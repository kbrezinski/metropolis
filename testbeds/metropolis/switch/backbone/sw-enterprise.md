# Metropolis enterprise-side switch

GNS3 switch role: Layer 2 enterprise-side segment attached to the DMZ/boundary router.

| Port role | Mode | VLAN | Network |
|---|---|---|---|
| `router-uplink` | Access/untagged | Default | `MET-OT-DMZ` eth2, `10.30.10.1/24` |
| `enterprise-host-1` | Access/untagged | Default | `10.30.10.0/24` |

TODO(Metropolis): Map roles to actual GNS3 ports. Firewall policy on the router is not configured yet.
