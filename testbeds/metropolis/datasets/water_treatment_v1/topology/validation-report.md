# Metropolis v1 topology validation

**Result: PASS (6/6 checks passed)**

Regenerate this report from the repository root with:

```bash
uv run python scripts/validate_metropolis_topology.py --report testbeds/metropolis/datasets/water_treatment_v1/topology/validation-report.md
```

## What the checks mean

| Check | What it compares | Result |
|---|---|---|
| Address plan: unique, valid, non-overlapping IPv4 networks | No mismatches found | PASS |
| Device inventory: site, subnet, IP, gateway, and environment | No mismatches found | PASS |
| Router interfaces and VLAN subinterfaces match address plan | No mismatches found | PASS |
| Static routes use reachable next hops and planned destinations | No mismatches found | PASS |
| Switch subnet references and trunk VLANs match plan | No mismatches found | PASS |
| All device service targets resolve to inventoried nodes | No mismatches found | PASS |

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

## Corrections made during this review

- Matched the reservoir and raw-water device inventory site IDs to the address plan.
- Added VLAN 30 to the OT services network and recorded the reserved reservoir IPsec overlay `10.20.254.16/30`.
- Changed the DMZ switch overview to say untagged, matching the physical router interface and switch port spec; this segment does not use VLAN 40.

## Scope and limitations

This validates consistency across the checked-in YAML, VyOS command text, and switch Markdown. A blank VLAN means a standalone untagged subnet. It does not execute VyOS configuration, confirm GNS3 cabling or port numbers, test packet reachability, or validate firewall/IPsec behavior. The two overlay `/30`s are reservations and intentionally have no configured router interfaces yet.
