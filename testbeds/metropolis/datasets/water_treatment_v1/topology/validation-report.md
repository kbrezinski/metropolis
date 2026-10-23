# water_treatment_v1 topology validation

**Result: PASS (16/16 checks passed)**

Regenerate this report from the repository root with:

```bash
uv run python scripts/validate_metropolis_topology.py --report testbeds/metropolis/datasets/water_treatment_v1/topology/validation-report.md
```

## What the checks mean

| Check | What it compares | Result |
|---|---|---|
| Documents conform to their JSON schemas | No mismatches found | PASS |
| Topology documents describe the same testbed | No mismatches found | PASS |
| Address plan: unique, valid, non-overlapping IPv4 networks | No mismatches found | PASS |
| Device inventory: site, subnet, IP, gateway, and environment | No mismatches found | PASS |
| Router interfaces and VLAN subinterfaces match address plan | No mismatches found | PASS |
| OSPF advertises connected networks without static routes | No mismatches found | PASS |
| Router syslog destination matches the inventoried receiver | No mismatches found | PASS |
| Bundled MQTT broker matches default certificate names and addresses | No mismatches found | PASS |
| Switch subnet references and trunk VLANs match plan | No mismatches found | PASS |
| VLAN port membership and router link interfaces match the plan | No mismatches found | PASS |
| All device service targets resolve to inventoried nodes | No mismatches found | PASS |
| HMI cell and telemetry settings match SCADA targets | No mismatches found | PASS |
| Routed SCADA and HMI service paths are permitted by policy | No mismatches found | PASS |
| Link plan matches the inventory, address plan, and router interfaces | No mismatches found | PASS |
| Inventoried devices are cabled or explicitly marked uncabled | No mismatches found | PASS |
| Routers and switches match their scripts, specs, and the link plan | No mismatches found | PASS |

## Address plan cross-reference

This is the central lookup table. Match each network to the router interface and switch/VLAN entries in the corresponding files.

| Network ID | Site | Zone | Kind | CIDR | Gateway | VLAN |
|---|---|---|---|---|---|---:|
| plant-intake | treatment_plant | levels_0_1 | lan | `10.20.10.0/24` | 10.20.10.1 | 10 |
| plant-dosing | treatment_plant | levels_0_1 | lan | `10.20.11.0/24` | 10.20.11.1 | 11 |
| plant-filtration | treatment_plant | levels_0_1 | lan | `10.20.12.0/24` | 10.20.12.1 | 12 |
| plant-disinfection | treatment_plant | levels_0_1 | lan | `10.20.13.0/24` | 10.20.13.1 | 13 |
| reservoir | reservoir_booster | levels_0_1 | lan | `10.20.14.0/24` | 10.20.14.1 | — |
| raw-water | raw_water_lift | levels_0_1 | lan | `10.20.15.0/24` | 10.20.15.1 | — |
| scada | treatment_plant | level_3 | lan | `10.20.20.0/24` | 10.20.20.1 | 20 |
| engineering | treatment_plant | level_3 | lan | `10.20.21.0/24` | 10.20.21.1 | 21 |
| historian | treatment_plant | level_3 | lan | `10.20.22.0/24` | 10.20.22.1 | 22 |
| mqtt | treatment_plant | level_3 | lan | `10.20.23.0/24` | 10.20.23.1 | 23 |
| ot-services | treatment_plant | level_3 | lan | `10.20.30.0/24` | 10.20.30.1 | 30 |
| ot-dmz | treatment_plant | level_3_5 | lan | `10.20.40.0/24` | 10.20.40.1 | — |
| plant-hmi | treatment_plant | level_2 | lan | `10.20.50.0/24` | 10.20.50.1 | 50 |
| dosing-hmi | treatment_plant | level_2 | lan | `10.20.51.0/24` | 10.20.51.1 | 51 |
| filtration-hmi | treatment_plant | level_2 | lan | `10.20.52.0/24` | 10.20.52.1 | 52 |
| disinfection-hmi | treatment_plant | level_2 | lan | `10.20.53.0/24` | 10.20.53.1 | 53 |
| enterprise | treatment_plant | level_4 | lan | `10.30.10.0/24` | 10.30.10.1 | — |
| attack-lab | treatment_plant | lab_test | lan | `10.99.10.0/24` | 10.99.10.1 | — |
| wan-underlay | inter_site | transport | underlay | `172.31.255.0/24` | — | — |
| plant-core-transit | treatment_plant | routing_transit | transit | `10.20.254.0/30` | — | — |
| core-dmz-transit | treatment_plant | routing_transit | transit | `10.20.254.4/30` | — | — |
| core-wan-transit | treatment_plant | routing_transit | transit | `10.20.254.8/30` | — | — |
| ipsec-reserved | inter_site | transport | overlay | `10.20.254.12/30` | — | — |
| ipsec-reservoir-reserved | inter_site | transport | overlay | `10.20.254.16/30` | — | — |

## Scope and limitations

This checks consistency among the selected dataset's YAML documents, its declared router scripts, and the declared links and VLAN paths. It also checks the generated switch reference and no-argument MQTT certificate defaults for the bundled dataset. A blank VLAN means an untagged subnet. Custom certificate names, addresses, and mounts are outside this check. It does not execute router or switch configuration, verify physical GNS3 cabling, test packet reachability or Layer 2 isolation, or validate firewall/IPsec behavior.
