# Metropolis switch configurations

These files define the Layer 2 port and VLAN plan for the Metropolis GNS3 testbed. They are configuration specifications for the GNS3 Ethernet switch appliance; they are not executable VyOS shell scripts. Assign the listed VLAN mode and VLAN ID to each GNS3 switch port in the appliance settings.

## Port mapping

Port labels such as `router-uplink` and `device-1` are roles, not literal GNS3 port numbers. Record the actual GNS3 port numbers after cabling the topology. Trunk VLANs must match the router subinterfaces. For an access port, assign one untagged VLAN. For a trunk, allow only the listed VLANs.

| Switch config | Router/uplink | VLANs / subnet | Intended endpoints |
|---|---|---|---|
| `backbone/sw-plant-aggregation.md` | `MET-PLANT` trunk | 10-13, 50-53 | Plant cell access switches and HMI access switches |
| `backbone/sw-operations-aggregation.md` | `MET-OT-CORE` trunk | 20-23, 30 | SCADA, engineering, historian, MQTT, OT services access switches |
| `backbone/sw-wan-underlay.md` | `MET-OT-WAN` and remote routers | Shared underlay `172.31.255.0/24` (untagged) | Simulated routed WAN links |
| `backbone/sw-dmz.md` | `MET-OT-DMZ` eth1 (untagged) | 40 / `10.20.40.0/24` | DMZ hosts |
| `backbone/sw-enterprise.md` | `MET-OT-DMZ` eth2 (untagged) | `10.30.10.0/24` | Enterprise-side hosts |
| `backbone/sw-attack-test.md` | `MET-OT-DMZ` eth3 (untagged) | `10.99.10.0/24` | Isolated attack-generation hosts |
| `locations/sw-reservoir-access.md` | `MET-RESERVOIR` eth0 (untagged) | 14 / `10.20.14.0/24` | Reservoir/booster RTU, sensors, actuators |
| `locations/sw-raw-water-access.md` | `MET-RAW-WATER` eth0 (untagged) | 15 / `10.20.15.0/24` | Raw-water lift RTU, sensors, actuators |

## Pending configuration

- TODO(Metropolis): Fill in GNS3 port numbers and endpoint-to-port assignments in each switch spec.
- TODO(Metropolis): Configure and verify the matching VLANs on the plant and operations router trunks.
- TODO(Metropolis): Decide whether management access is in-band or uses a separate management network; no switch management IPs are assigned yet.
- TODO(Metropolis): Add port security, unused-port shutdown, and any required Layer 2 protections after selecting the appliance capabilities.
- TODO(Metropolis): Firewall policy and IPsec are router-layer items and remain unconfigured; switch VLAN membership does not enforce those policies.
