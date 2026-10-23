# Metropolis research plan

This is a living, concise plan. Record new ideas and decisions here when we discuss future work. Tasks are ordered from higher to lower priority. Only category 1 is active; categories 2–4 are parked until the user explicitly asks to work on them.

## 1. Testbed and attack scripts — current focus

Expand the GNS3 water-treatment network one cell at a time. Prioritize credible packet timing, polling rates, and value-change patterns; physically accurate values and cross-cell process coupling are not required. Check images, links, subnets, routes, and capture coverage for each addition. Retain syslog events and collection metadata for later analysis; treat ordinary Docker output as simulator diagnostics.

The intake, dosing, filtration, and disinfection cells have declared and implemented nodes. Their HMIs send setpoints through SCADA and receive PLC register readback acknowledgements. The reservoir and raw-water sites now have networked level instruments and pump drives around their RTUs, with read-only central SCADA polling. Local devices use Modbus TCP and cyclic EtherNet/IP; the WAN carries routed RTU polls without IPsec. These paths have offline checks, but startup, packet timing, and alarms have not been verified in live GNS3. Measurements are simple and independent of physical process behavior.

An on-demand Daybreak Blue security review tool discovers a selected dataset's settings, router files, and attack catalog to record SDK findings separately from theoretical attack paths. It does not verify the running GNS3 network.

### Testbed features

- [ ] Add route-based site-to-site IPsec tunnels between the WAN hub and both remote-site routers. Route operational traffic through the tunnels, keep the simulated WAN as the untrusted underlay, and add offline checks for tunnel configuration and routed paths.
- [ ] Give the declared DMZ and enterprise networks a purposeful, controlled data or administration path, such as a DMZ relay and enterprise consumer; define the service and firewall flows before adding hosts.
- [ ] Explore paired plant and operations aggregation switches with dual-uplink access switches, then consider DMZ, WAN, and remote-site redundancy. Revisit router uplinks and Open vSwitch RSTP before adding loops; these are proposals, not declared nodes.
- [ ] Consider one additional interoperable protocol where it has a realistic peer and observable traffic, such as OPC UA for supervisory exchange or DNP3 for remote telemetry. Separately decide whether CoAP security/features or MQTT ACLs serve a research scenario; document simulator fidelity limits.
- [ ] Consider a controlled RDP endpoint for future remote administration traffic. Define its host role, normal TCP/UDP behavior, permitted path, and capture point before adding it; the imported RDP-UDP attack recipe is only a reference image and has no current target.

### GNS3 tooling and cleanup

- [ ] Define an expected normal-flow and capture-point matrix for populated cells and remote RTUs: endpoints, protocols, polling and cyclic rates, fault traffic, and local versus routed links. Mark declared but unpopulated networks separately; keep Docker output as simulator diagnostics.
- [ ] Make the GNS3 builder detect stale image, environment, and adapter settings when it reuses a node by name; plan how to report or reconcile drift before expanding the inventory.
- [ ] Extend `protocol_profiles/` beyond the versioned Modbus attack action map with the full register maps, MQTT topics, EtherNet/IP assemblies, service settings, and timing defaults; check profiles against inventory and simulator configuration. Preserve stable per-cell SCADA polling rates as targets are added.
- [ ] Add the existing firewall policy as an optional document in `metropolis.topology`, including load/save and validation; have the GNS3 compiler consume that model instead of reading the YAML separately.

### Attack scripts — later

- [ ] Add dedicated attack cases for newly populated cells after their normal packet patterns are confirmed. Keep this separate from adding the normal device traffic.

## 2. Data processing and model development — future

Existing PCAP extraction and cleaning are an initial foundation. Record future feature, labeling, dataset, and model ideas here when discussed. Do not begin this work until prompted.

## 3. Experimentation, ablation, and model comparison — future

Record proposed experiment designs and comparison questions here when discussed. Do not begin this work until prompted.

## 4. Paper authoring — future planning scope

Focus the paper on two contributions: (1) the reproducible Metropolis OT water-treatment testbed and (2) its versioned traffic dataset. Describe how scenarios generate traffic, how each capture's scenario and source are recorded, and how labels are assigned and checked. Distinguish planned capabilities from implemented and runtime-verified results; do not claim that captures or labels have been validated until that work is complete. Attack detection and detector evaluation are future work, outside this paper's current scope.
