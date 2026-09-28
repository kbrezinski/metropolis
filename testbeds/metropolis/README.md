# Metropolis testbed

Metropolis is the repository's first GNS3 water-treatment OT/ICS testbed. Its
routers, switches, device models, and versioned dataset are scoped to this
directory so additional testbeds can be added alongside it.

- [`router/`](router/) contains VyOS configuration drafts organized by backbone
  and location.
- [`switch/`](switch/) contains GNS3 VLAN and port specifications.
- [`devices/`](devices/) contains Docker-based emulated device models.
- [`datasets/water_treatment_v1/`](datasets/water_treatment_v1/) contains this
  testbed's initial topology and device inventory.

The concrete address allocations belong to Metropolis and are recorded in its
dataset topology. Their document format follows the shared
[`address-plan.schema.json`](../../schemas/address-plan.schema.json); other
testbeds can use that format with independently selected ranges.
