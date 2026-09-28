# Metropolis plant aggregation switch

GNS3 switch role: Layer 2 aggregation between `MET-PLANT` and plant/HMI access switches.

| Port role | Mode | VLANs | Notes |
|---|---|---|---|
| `router-uplink` | Trunk | 10-13, 50-53 | Connect to `MET-PLANT` eth1; router uses matching 802.1Q subinterfaces |
| `cell-intake-uplink` | Trunk | 10 | Plant cell access switch uplink |
| `cell-dosing-uplink` | Trunk | 11 | Plant cell access switch uplink |
| `cell-filtration-uplink` | Trunk | 12 | Plant cell access switch uplink |
| `cell-disinfection-uplink` | Trunk | 13 | Plant cell access switch uplink |
| `hmi-intake-uplink` | Trunk | 50 | HMI access switch uplink |
| `hmi-dosing-uplink` | Trunk | 51 | HMI access switch uplink |
| `hmi-filtration-uplink` | Trunk | 52 | HMI access switch uplink |
| `hmi-disinfection-uplink` | Trunk | 53 | HMI access switch uplink |

TODO(Metropolis): Map each role to the actual GNS3 port number. TODO: Confirm the appliance supports the listed trunk VLAN behavior.
