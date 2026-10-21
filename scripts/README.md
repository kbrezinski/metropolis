# Repository scripts

This directory contains dataset-processing, topology-validation, and bounded
lab experiment utilities. See [`attacks/README.md`](attacks/README.md) for the
toolkit and [`attacks/SERVICE_TARGETS.md`](attacks/SERVICE_TARGETS.md) for service
targets and Gotham adaptation notes. An automated GNS3 topology builder remains
future work.

## GNS3 implementation references

Metropolis' GNS3 design is currently expressed as device models and
configuration specifications rather than a one-command topology deployment:

- Buildable Docker device models: `testbeds/metropolis/devices/`
- Router configuration scripts: `testbeds/metropolis/router/`
- Switch configuration notes: `testbeds/metropolis/switch/`
- Planned device instances: `testbeds/metropolis/datasets/water_treatment_v1/device_instances/initial_devices.yaml`
- Address and subnet plan: `testbeds/metropolis/datasets/water_treatment_v1/topology/address-plan.yaml`
- Consistency check: `python scripts/validate_metropolis_topology.py`

When creating a GNS3 project, use stable logical roles for equivalent
experiment components across testbeds. For example, an attacker node could be
named `LAB-ATTACKER-01`, while the Metropolis MQTT broker is
`MET-MQTT-BROKER-01`. Record the mapping, actual project name, node names,
addresses, image versions, and routes with each experiment.

The topology validator checks the checked-in plans; it does not connect to
GNS3 or confirm that a running project matches those plans. Start and validate
nodes and routes in GNS3 before capturing traffic.

## Dataset-processing pipeline

[`run_gotham_pipeline.py`](run_gotham_pipeline.py) prepares downloaded Gotham
network packet feature CSVs. It is separate from the Metropolis GNS3 topology
and from attack execution. See the root README for its inputs and outputs.

### Metropolis PCAP extraction (stage 1)

Install Wireshark's `tshark` command, then extract a completed capture run:

```bash
uv run python scripts/extract_metropolis_features.py testbeds/metropolis/datasets/water_treatment_v1/captures/<run-id>/manifest.json
```

The command reads the capture manifest's `local_file` entries, verifies any
recorded PCAP SHA256 hashes, and runs TShark over every packet in each available
capture. It does not select devices, subnets, IP addresses, or attack labels.
The output is `runs/<run-id>/features/raw/<capture-point>.csv` in the same
versioned dataset, plus `runs/<run-id>/features/extraction_report.json` with
field names, source and output hashes, row counts, link IDs, UTC timestamps,
and per-capture failures. A failed capture does not prevent extraction of the
others. The command exits with a nonzero status if the report is incomplete.
These generated run files are excluded from Git.

Edit or copy [`features/packet_fields.json`](features/packet_fields.json) to
choose TShark fields; pass the copy with `--fields-config`. `--output-dir` can
override the default report/output location, and `--tshark` selects a TShark
executable. TShark versions may differ in supported field names. An unsupported
field is reported as an extraction failure; adjust the field list for the
installed version. Repeated fields use their first occurrence. CSV values are
raw TShark field values; missing protocol fields stay blank, and no cleaning,
filtering, labeling, deduplication, or sampling happens in this stage. Captures
on different links can observe the same packet, so rows across files should
not be assumed unique.

### Metropolis feature cleaning (stage 2)

After extraction, clean the raw CSVs with:

```bash
uv run python scripts/clean_metropolis_features.py testbeds/metropolis/datasets/water_treatment_v1/runs/<run-id>/features/extraction_report.json
```

This reads only the extraction report's successful outputs, verifies each raw
CSV hash, and writes `features/cleaned/<capture-point>.csv` plus
`features/cleaning_report.json`. The report records retained/dropped fields,
input/output row counts, missing-value counts, hashes, UTC times, and failures.
It keeps working on other captures if one fails, and exits nonzero for an
incomplete run. The raw CSVs are never changed.

The default [`features/cleaning_rules.json`](features/cleaning_rules.json)
trims surrounding whitespace and turns whitespace-only cells into blanks. It
keeps all rows and fields, including missing protocol fields and repeated
packets. It rejects malformed CSV rows rather than silently discarding them.
For a different analysis, copy the rules file and pass `--rules PATH`:

- `keep_fields`: selected field names in output order, or `null` for all fields.
- `drop_fields`: fields to remove after selection.
- `drop_rows_missing_fields`: remove a row when any named field is blank.
- `fill_missing`: replace blank values in named fields with specified strings.
- `strip_whitespace`: trim surrounding whitespace when `true`.

Rules reference field names from the extraction report, so there are no fixed
IP addresses, nodes, or network ranges. Row removal and missing-value filling
are opt-in because blank fields often mean that a protocol does not apply to a
packet. No statistical feature pruning, deduplication, sampling, labeling, or
cross-capture merging occurs at this stage. `--output-dir` selects a different
cleaned-output/report directory.
