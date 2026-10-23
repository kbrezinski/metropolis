# Repository scripts

Run commands from the repository root after `uv sync --group dev`.
GNS3 and attack examples use Ubuntu/Bash.

| Task | Entry point and guide |
|---|---|
| Build the lab, capture traffic, collect logs | [`gns3/run.py`](gns3/README.md) |
| Generate attack traffic | [`attacks/run.py`](attacks/README.md) |
| Review declared network security | [`security/run.mjs`](security/README.md) |
| Validate a dataset offline | `uv run python scripts/validate_metropolis_topology.py` |
| Create a topology in Python | [`build_a_topology.py`](build_a_topology.py) |
| Process Metropolis PCAPs | Steps below |
| Process downloaded Gotham CSVs | `uv run python scripts/run_gotham_pipeline.py --help` |

## Dataset-processing pipeline

Install Wireshark's `tshark` command. Choose a completed capture run, then use
the same dataset and run ID for both stages:

```bash
DATASET=testbeds/metropolis/datasets/water_treatment_v1
RUN_ID=baseline_001
```

Replace `baseline_001` with the capture run you want to process.

### 1. Extract packet features

```bash
uv run python scripts/extract_metropolis_features.py \
  "$DATASET/captures/$RUN_ID/manifest.json"
```

The extractor verifies recorded PCAP hashes and reads every packet from each
available capture. It writes:

- `runs/<run-id>/features/raw/<capture-point>.csv`
- `runs/<run-id>/features/extraction_report.json`

The report records fields, row counts, hashes, link IDs, UTC times, and failures.
Rows retain raw TShark values; repeated fields use their first occurrence.
Captures from different links may contain the same packet.

To customize extraction, copy [packet_fields.json](features/packet_fields.json)
and pass `--fields-config PATH`. Use `--tshark PATH` for a different executable
or `--output-dir PATH` for another destination. Unsupported TShark fields are
reported as failures.

### 2. Clean the extracted CSVs

```bash
uv run python scripts/clean_metropolis_features.py \
  "$DATASET/runs/$RUN_ID/features/extraction_report.json"
```

The cleaner verifies raw CSV hashes and writes
`features/cleaned/<capture-point>.csv` and `features/cleaning_report.json`.
The raw files are preserved.

Default cleaning trims whitespace and turns whitespace-only cells into blanks.
It keeps all rows and fields and rejects malformed CSV rows. Copy
[cleaning_rules.json](features/cleaning_rules.json) and pass `--rules PATH`
to customize:

| Rule | Effect |
|---|---|
| `keep_fields` | Select fields and their order; `null` keeps all |
| `drop_fields` | Remove named fields |
| `drop_rows_missing_fields` | Drop rows missing any named field |
| `fill_missing` | Fill blanks in named fields |
| `strip_whitespace` | Trim values when `true` |

Both stages continue with other captures after a failure and exit nonzero when
their report is incomplete. Neither stage assigns attack labels, deduplicates,
or merges captures. Settings use field names, with no fixed node addresses.
