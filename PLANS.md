# Metropolis research plan

This is a living, concise plan. Record new ideas and decisions here when we discuss future work. Only category 1 is active. Categories 2–4 are parked until the user explicitly asks to work on them.

## 1. Testbed and attack scripts — current focus

Expand the GNS3 water-treatment network one cell at a time, with bounded attack cases alongside normal traffic. Prioritize credible packet timing, polling rates, and value-change patterns; physically accurate values and cross-cell process coupling are not required. Check images, links, subnets, routes, and capture coverage for each addition.

**Network fundamentals as the topology expands:** Router scripts now use OSPF area 0 for inter-router routing, with all connected LANs advertised passively. Live route selection and firewall behavior remain to be checked in GNS3.

- [ ] Consider STP when redundant switch links are added; the current Open vSwitch configuration does not enable it.

**Next milestone — intake:** Finish the declared duty pump drive, standby drive, and valve controller as separate simulated nodes. Inventory, links, simulator code, and focused tests exist, but these devices are not a completed testbed capability. Add configurable cyclic EtherNet/IP I/O and check its expected packet pattern.

**Then — dosing:** Add a PLC, networked analyzer, duty and standby drives, and HMI; extend central SCADA polling to the cell. Reuse Modbus TCP and EtherNet/IP on the existing dosing and HMI networks. Add bounded attack cases and verify normal and attack traffic in captures.

**Later ideas:** Filtration, disinfection, remote sites, and broader network expansion. Discuss and scope each before implementation.

**Telemetry:** Reuse the existing central syslog receiver for network-delivered device events. Treat Docker output as simulator diagnostics, not research telemetry. Verify source attribution and report missing sources.

**Low priority:** Add the existing firewall policy as an optional document in `metropolis.topology`, including load/save and validation; have the GNS3 compiler consume that model instead of reading the YAML separately.

## 2. Data processing and model development — future

Existing PCAP extraction and cleaning are an initial foundation. Record future feature, labeling, dataset, and model ideas here when discussed. Do not begin this work until prompted.

## 3. Experimentation, ablation, and model comparison — future

Record proposed experiment designs and comparison questions here when discussed. Do not begin this work until prompted.

## 4. Paper authoring — future planning scope

Focus the paper on two contributions: (1) the reproducible Metropolis OT water-treatment testbed and (2) its versioned traffic dataset. Describe how scenarios generate traffic, how each capture's scenario and source are recorded, and how labels are assigned and checked. Distinguish planned capabilities from implemented and runtime-verified results; do not claim that captures or labels have been validated until that work is complete. Attack detection and detector evaluation are future work, outside this paper's current scope.
