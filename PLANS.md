# Metropolis research plan

This is a living, concise plan. Record new ideas and decisions here when we discuss future work. Tasks are ordered from higher to lower priority. Only category 1 is active; categories 2–4 are parked until the user explicitly asks to work on them.

## 1. Testbed and attack scripts — current focus

Expand the GNS3 water-treatment network one cell at a time. Prioritize credible packet timing, polling rates, and value-change patterns; physically accurate values and cross-cell process coupling are not required. Check images, links, subnets, routes, and capture coverage for each addition.

- [ ] Add the dosing cell: PLC, networked analyzer, duty and standby drives, HMI, and central SCADA polling. Reuse Modbus TCP and EtherNet/IP; verify normal traffic in captures.
- [ ] Verify source attribution and missing-source reporting for the central syslog receiver. Treat Docker output as simulator diagnostics, not research telemetry.
- [ ] Scope filtration, disinfection, remote sites, and broader network expansion before implementation.
- [ ] Explore paired plant and operations aggregation switches with dual-uplink access switches, then consider DMZ, WAN, and remote-site redundancy. Revisit the one-uplink topology assumptions, router uplinks, and native Open vSwitch RSTP before adding loops. These are proposals, not declared nodes.
- [ ] Add the existing firewall policy as an optional document in `metropolis.topology`, including load/save and validation; have the GNS3 compiler consume that model instead of reading the YAML separately.
- [ ] Scope future attack scripts separately, including cases for new cells after normal traffic is established.

## 2. Data processing and model development — future

Existing PCAP extraction and cleaning are an initial foundation. Record future feature, labeling, dataset, and model ideas here when discussed. Do not begin this work until prompted.

## 3. Experimentation, ablation, and model comparison — future

Record proposed experiment designs and comparison questions here when discussed. Do not begin this work until prompted.

## 4. Paper authoring — future planning scope

Focus the paper on two contributions: (1) the reproducible Metropolis OT water-treatment testbed and (2) its versioned traffic dataset. Describe how scenarios generate traffic, how each capture's scenario and source are recorded, and how labels are assigned and checked. Distinguish planned capabilities from implemented and runtime-verified results; do not claim that captures or labels have been validated until that work is complete. Attack detection and detector evaluation are future work, outside this paper's current scope.
