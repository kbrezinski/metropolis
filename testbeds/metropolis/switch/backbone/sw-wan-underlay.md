# Metropolis simulated WAN underlay switch

GNS3 switch role: Shared Layer 2 underlay for the WAN hub and remote-site routers. This represents the simulated provider network; it is not the IPsec overlay.

| Port role | Mode | VLAN | Address planned on connected router |
|---|---|---|---|
| `wan-hub` | Access/untagged | Default | `MET-OT-WAN` eth1: `172.31.255.1/24` |
| `raw-water-site` | Access/untagged | Default | `MET-RAW-WATER` eth1: `172.31.255.11/24` |
| `reservoir-site` | Access/untagged | Default | `MET-RESERVOIR` eth1: `172.31.255.21/24` |

TODO(Metropolis): Map roles to actual GNS3 ports. TODO: Configure IPsec peers/VTIs and WAN firewall policy on the routers; this shared switch provides no encryption or traffic filtering.
