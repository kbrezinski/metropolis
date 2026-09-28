# Testbeds

Each network environment lives in its own directory. Keep its router and switch
configuration, device deployment, and versioned dataset together so a future
testbed can be added without mixing its topology with another one.

```text
testbeds/<testbed_id>/
├── router/
├── switch/
├── devices/
└── datasets/<dataset_name>_v<n>/
```

Metropolis is the first testbed and is documented in [`metropolis/`](metropolis/).
Reusable document structures live in the repository-level `schemas/` directory.
For example, [`address-plan.schema.json`](../schemas/address-plan.schema.json)
can validate address plans while each testbed chooses its own CIDRs. Reuse an
address allocation pattern only when it serves the research comparison; the
schema itself does not prescribe an address range.
