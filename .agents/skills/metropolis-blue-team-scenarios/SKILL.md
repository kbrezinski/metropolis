---
name: metropolis-blue-team-scenarios
description: Analyze a selected Metropolis testbed's declared network configuration and attack catalog to draft evidence-linked, theoretical blue-team attack scenarios. Use for testbed threat-path planning, not repository vulnerability scans or live exercises.
---

# Metropolis blue-team scenarios

Produce a defensive assessment of the **declared testbed**, followed by plausible attack paths. This is configuration analysis. Do not invoke Codex Security scans, contact GNS3 or a compute, execute repository code, attack scripts, binaries, or generated commands, or inspect implementation code for vulnerabilities. Do not describe a hypothetical compromise as observed.

## Select and read the evidence

- Discover datasets under `testbeds/*/datasets/*` and catalogs under `scripts/attacks/scenarios/*.json`. Use the user's selections; select the only available choice when unambiguous. If several remain, ask which dataset or catalog to assess. Do not assume a fixed version, node count, address, or malware role.
- Read the selected dataset's YAML in `topology/`, `device_instances/`, and `protocol_profiles/`. Read router configuration files **referenced by the selected infrastructure inventory** as intended settings. Use the attack catalog only to identify scenario families and themes. The catalog and bundled support nodes do not prove that an exploit works or fix where an attacker, infected host, or command server must be.
- Treat the selected configuration as the sole evidence for network claims. Do not use attack implementations, Dockerfiles, device source, README prose, plans, tests, or the security scanner's findings to infer deployed exposure. Official ATT&CK and OWASP material may be consulted to classify a step, not to establish a Metropolis fact. Do not reproduce passwords, tokens, keys, or other credential values found in inventory settings.
- Record the dataset and catalog paths, version or revision when available, review date, and missing or contradictory inputs. Cite file paths and specific entries or lines for every named node, address, service, network boundary, and policy claim.

## Build the declared network view

Inventory hosts, roles, addresses, subnets, VLANs, links, switch port roles, router interfaces, routing and tunnels, service protocols and ports, and firewall selectors. Distinguish actual switch appliances from logical access segments, physical GNS3 links from logical overlays, and uncabled or spare declarations from attached hosts. A declared link is not proof of live cabling.

For a proposed source-to-destination step, check the source subnet, local Layer 2 path or routed path, router and tunnel settings, then applicable baseline firewall flow by source network/CIDR, destination network/CIDR, transport, and port. Same-VLAN traffic bypasses the routed firewall. Separate baseline permissions from experiment-only exceptions. Mark each step **permitted**, **blocked**, or **unknown under declared settings**, with the rule or missing evidence that determines it. An unmatched allow rule is not proof of an effective block unless the allowed configuration establishes the default action. Never infer that declared routing, IPsec, ACLs, or firewall settings were applied in live GNS3. Flag conflicts between address, inventory, link, router, and policy declarations rather than resolving them silently.

## Develop the scenarios

For a full review, cover every distinct family in the selected attack catalog, grouping entries from the same family. Model credible real-world variants rather than replaying the bundled bounded lab action. A Merlin or Mirai variant may begin on another plausible host and use other command infrastructure. For each scenario, make the foothold and any external access an explicit assumption; if the configuration does not show an ingress path, start from a conditional compromised node. Do not turn an unknown or blocked edge into a successful lateral-movement step. If a catalog family gives too little detail to support a path, report that limit rather than inventing a reachable target.

Describe the conditional cyber kill chain from foothold through discovery, access, movement, command and control where relevant, and potential OT effect. Name candidate source and target nodes, their declared IPs, protocols and ports, prerequisites, path status, and likely network or syslog observables. Distinguish an exposed service from a demonstrated exploitable flaw. Explain which controls constrain the path and which paths need live verification. Rank scenarios by declared reachability, prerequisites, and possible impact; do not present a numerical compromise probability derived only from configuration.

Map steps to applicable **MITRE ATT&CK for ICS** or **Enterprise** tactics and techniques using current official definitions; identify the matrix and technique ID. Use an **OWASP** category with its edition only for a relevant web, API, or other matching application risk. Leave either mapping empty when unsupported rather than forcing an analogy. Do not assign a CVE from a protocol or product name alone; require a supported product/version match and an authoritative advisory, otherwise state that no CVE was established.

## Deliverable

Give a short network and trust-boundary summary and a compact baseline flow matrix covering the declared routed permissions and relevant denied or uncertain boundaries. Then give a table with one row per scenario: family, assumed foothold, candidate nodes/IPs, permitted and blocked/unknown movement, protocols, possible effect, and evidence. Follow with a compact step table for each scenario: kill-chain phase, source → destination, protocol/port, path status and prerequisite, ATT&CK/OWASP mapping if supported, evidence, and defensive observable. End with coverage of every catalog family, unresolved configuration gaps, and a clear statement that live behavior was not verified.

## Save every review

By default, save each completed review under the repository's sibling `../metropolis-security-results/blue-team/<testbed>/<dataset>/<UTC timestamp>_<short Git revision>/`. This shares the existing security review results root while keeping these configuration-only reviews in their own area. Use a user-specified output root when provided. Create a new run directory each time; do not overwrite an earlier review or silently switch destinations if the chosen root cannot be written.

Write `scenarios.md` for human review and `scenarios.json` with the same scenario IDs, step statuses, assumptions, technique mappings, and evidence. Write `review-metadata.json` with the selected dataset and catalog, UTC creation time, Git revision and dirty state when available, reviewed input paths and SHA-256 hashes, catalog-family coverage, output paths, and completion or failure status. Never put credential values in these files. If the review is interrupted or some families cannot be assessed, retain the partial files and mark the missing coverage explicitly. Return links to the saved files in the response.
