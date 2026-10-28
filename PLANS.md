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
- [ ] Cable the Mirai listener, HTTP/TFTP loaders, and example bot into the isolated attack segment; verify reachability and capture coverage.
- [ ] Move Mirai choreography from the host runner into the declared nodes: bounded bot probing and Telnet attempts, listener-to-loader reporting, inert delivery, and observable C&C command delivery. Distinguish each simulated stage from an actual infection.
- [ ] Extend the Merlin scenario with observable, controlled tool-transfer and attack stages from the agent node; verify their traffic and effects without arbitrary command execution.
- [ ] Decide whether Mirai or Merlin needs real binaries for the intended research claims, and define isolation and evidence requirements before considering that work.

## 2. Data processing and model development — future

Build on existing PCAP extraction and cleaning when requested. Record feature, labeling, dataset, and model ideas here.

## 3. Experimentation, ablation, and model comparison — future

Record experiment designs, ablations, and model comparisons here when discussed.

## 4. Paper authoring — future

Keep paper objectives, questions, and writing in [PAPER_PLAN.md](docs/paper/PAPER_PLAN.md).
