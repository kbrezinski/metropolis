# Metropolis research plan

Record future ideas and decisions here. Earlier items have higher priority. Only category 1 is active; categories 2–4 stay parked until requested. Remove completed tasks.

## 1. Testbed and attack scripts — current focus

Expand one cell at a time, prioritizing credible packet timing, polling, and value-change patterns over physical process accuracy. Check images, wiring, routing, firewall permissions, and capture coverage. Retain syslog as research telemetry and Docker output as simulator diagnostics. See [README.md](README.md) for current capabilities.

### Testbed features

- [ ] Add route-based site-to-site IPsec between the WAN hub and both remote sites. Route operational traffic through the tunnels over the untrusted WAN; check tunnel configuration and routes offline.
- [ ] Define a controlled DMZ-to-enterprise data or administration path, such as a relay and consumer. Specify services and firewall permissions before adding hosts.
- [ ] Explore redundant plant and operations aggregation switches with dual-uplink access switches, then other sites. Plan router uplinks and Open vSwitch RSTP before adding loops.
- [ ] Evaluate OPC UA or DNP3 where realistic peers and observable traffic exist. Assess CoAP security and MQTT ACLs for a defined research scenario; document protocol-model limits.
- [ ] Consider an RDP administration endpoint. Define its role, normal TCP/UDP traffic, firewall path, and capture point before adding it.

### GNS3 tooling and cleanup

- [ ] Define a normal-traffic and capture-point matrix: endpoints, protocols, polling/cyclic rates, fault traffic, and local/routed links. Distinguish populated networks from declarations without devices.
- [ ] Detect image, environment, and adapter drift when the builder reuses nodes; define how to report or reconcile it.
- [ ] Make the builder and lifecycle commands honor the selected `--dataset` consistently.
- [ ] Extend `protocol_profiles/` with register maps, MQTT topics, EtherNet/IP assemblies, service settings, and timing defaults. Validate against inventory and simulators; keep per-cell SCADA polling rates stable as targets grow.
- [ ] Add optional firewall-policy load/save and validation to `metropolis.topology`; make the GNS3 compiler consume that model.
- [ ] Consider automated image preparation and prerequisite checks on each compute, plus reproducible VyOS appliance setup.
- [ ] Consider explicit node placement across computes when capacity requires it; verify configuration, capture, and log attribution across them.

### Attack scripts — later

- [ ] Add scenarios for newly populated cells after normal traffic is established. Use `scripts/attacks/run.py`, user-selected launch timing, and attribution to the capture run ID.
- [ ] If needed, add a separate Merlin agent-origin attack-traffic scenario with a selected target and packet profile. The Gotham-inspired support sequence stops after inert transfer and `chmod`.
- [ ] Decide whether Mirai or Merlin needs real binaries for the intended research claims, and define isolation and evidence requirements before considering that work.

## 2. Data processing and model development — future

Build on existing PCAP extraction and cleaning when requested. Record feature, labeling, dataset, and model ideas here.

## 3. Experimentation, ablation, and model comparison — future

Record experiment designs, ablations, and model comparisons here when discussed.

### Standalone agent benchmark

The independent [agent benchmark](scripts/agent_bench/README.md) contains twelve pilot tasks, Stirrup 0.2.1 as its sole agent engine, protected Docker grading, human review, and count-based reports. Engine settings and installed dependency versions remain separate in reports. It has no GNS3 or Metropolis dataset dependency.

- [ ] On a Linux-container Docker host, build the supplied image and run the complete canned pilot plus opt-in Docker acceptance checks. Live Docker behavior could not be verified on the implementation host; do not treat mocked checks as isolation or packet evidence.
- [ ] Define the proposed full/partial/failure/refusal rubric before adding differently scoped tasks. Keep requested-solution adherence separate from functional correctness; the current finite packet contract remains full success when satisfied.

## 4. Paper authoring — future

Keep paper objectives, questions, and writing in [PAPER_PLAN.md](docs/paper/PAPER_PLAN.md).
