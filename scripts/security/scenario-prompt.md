After the security scan, create a separate *theoretical* topology analysis.
Use the selected dataset, router settings, and attack catalog as your source.
Produce exactly one scenario for every distinct catalog family listed below.
Group multiple catalog entries for one family into that scenario. The family
names and their catalog IDs must match the list exactly:

{{FAMILIES}}

For each family, describe a plausible initial foothold and prerequisites,
command infrastructure if relevant, step-by-step movement or target access,
protocols, path status under the *declared* topology/firewall, possible OT
effect, network observables, controls, and limits. The infected host or command
infrastructure may be different from any bundled lab bot, agent, or C2 node.
Mark steps as configured, inferred, or hypothetical. A blocked or unknown path
must stay blocked or unknown in the narrative. CVEs require evidence for exact
affected product and version plus an authoritative advisory; otherwise write
an empty CVE array. Do not inspect or execute attack implementation code,
contact GNS3, or alter repository files.

Write two files outside the repository, at these exact paths:

- JSON: {{JSON_PATH}}
- Markdown: {{MARKDOWN_PATH}}

The JSON must be one object with `schema_version: "1.0"`,
`review_kind: "theoretical"`, `generated_at_utc` in UTC ISO 8601, and a
`scenarios` array. Each scenario must contain:

- `family`: catalog family name.
- `catalog_ids`: all catalog IDs for that family.
- `title`, `initial_foothold`, `command_infrastructure`, and
  `potential_ot_impact`: nonempty strings.
- `prerequisites`, `observables`, `mitigations`, and `limitations`: arrays of
  strings. Use an empty array when none apply.
- `steps`: a nonempty array of objects with nonempty `source`, `destination`,
  `protocol`, and `description` strings; `path_status` equal to `permitted`,
  `blocked`, `same_vlan`, or `unknown`; `basis` equal to `configured`,
  `inferred`, or `hypothetical`; and `evidence_paths` as an array of selected
  repository-relative paths. Use an empty evidence array where no file proves
  the step.
- `cves`: an array of objects with `id`, `product`, `version_evidence`, and
  `source_url`. Use an empty array if no specific CVE is supportable.

The Markdown must begin with a brief scope statement, then a table headed
`| Family | Foothold | Path and status | Possible impact | Evidence |`.
Add a short section per family for the full chain, prerequisites, observable
traffic, controls, and uncertainty. Label the report theoretical and state
that live GNS3 behavior was not verified. Do not copy SDK findings into this
scenario report as confirmed findings.
