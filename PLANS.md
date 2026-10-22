# Metropolis research plan

This is a living, concise plan. Record new ideas and decisions here when we discuss future work. Only category 1 is active. Categories 2–4 are parked until the user explicitly asks to work on them.

## 1. Testbed and attack scripts — current focus

Expand the GNS3 water-treatment network one cell at a time, with bounded attack cases alongside normal traffic. Prioritize credible packet timing, polling rates, and value-change patterns; physically accurate values and cross-cell process coupling are not required. Check images, links, subnets, routes, and capture coverage for each addition.

**Network fundamentals — before cell expansion:**

- [ ] Check declared VLAN access/trunk membership and router interfaces with offline validation; the user will check runtime isolation in GNS3.
- [ ] Define and apply router firewall rules for permitted traffic between cells, operations, DMZ, and the attack-test network; test allowed and blocked paths without requiring a live GNS3 run.
- [ ] Consider OSPF when alternate routed paths are added; keep static routes in the current topology.
- [ ] Consider STP when redundant switch links are added; the current Open vSwitch configuration does not enable it.

**Next milestone — intake:** Finish the declared duty pump drive, standby drive, and valve controller as separate simulated nodes. Inventory, links, simulator code, and focused tests exist, but these devices are not a completed testbed capability. Add configurable cyclic EtherNet/IP I/O and check its expected packet pattern; the user will validate it in GNS3 offline.

**Then — dosing:** Add a PLC, networked analyzer, duty and standby drives, and HMI; extend central SCADA polling to the cell. Reuse Modbus TCP and EtherNet/IP on the existing dosing and HMI networks. Add bounded attack cases and verify normal and attack traffic in captures.

**Later ideas:** Filtration, disinfection, remote sites, and broader network expansion. Discuss and scope each before implementation.

**Telemetry:** Reuse the existing central syslog receiver for network-delivered device events. Treat Docker output as simulator diagnostics, not research telemetry. Verify source attribution and report missing sources.

## 2. Data processing and model development — future

Existing PCAP extraction and cleaning are an initial foundation. Record future feature, labeling, dataset, and model ideas here when discussed. Do not begin this work until prompted.

## 3. Experimentation, ablation, and model comparison — future

Record proposed experiment designs and comparison questions here when discussed. Do not begin this work until prompted.

## 4. Paper authoring — current planning scope

Focus the paper on two contributions: (1) the reproducible Metropolis OT water-treatment testbed and (2) its versioned traffic dataset. Describe how scenarios generate traffic, how each capture's scenario and source are recorded, and how labels are assigned and checked. Distinguish planned capabilities from implemented and runtime-verified results; do not claim that captures or labels have been validated until that work is complete. Attack detection and detector evaluation are future work, outside this paper's current scope.

## Progress and updates

- [x] 2026-10-22: Create the plan and set category 1 as the active scope.
- [x] 2026-10-22: Choose intake completion, then dosing; prioritize realistic network traffic over process values.
- [x] 2026-10-22: Add network fundamentals ahead of further cell expansion.
- [x] 2026-10-22: Code the dataset firewall policy, offline route/cabling checks, VyOS rule generation, and recorded temporary attack exceptions. User runtime verification remains pending.
- [ ] Complete the intake implementation and automated checks; the user will validate it in GNS3 offline.
- [ ] Implement and verify the dosing cell and its bounded attack cases.

Status terms: **planned** is a decision; **coded** means repository files exist; **runtime-verified** means the user has observed the behavior in GNS3 offline. Keep automated checks and runtime verification distinct. Add a dated, brief note when decisions or status change.
