# Declared topology review context

Treat the selected dataset documents and router configuration files as the
**intended** GNS3 network settings. They do not prove that GNS3 has built the
nodes, applied the settings, or carried packets. Do not contact GNS3, run
attacks, or claim live validation.

Review the selected topology, inventory, protocol, firewall, and router files
together. Determine routing, VLAN boundaries, firewall enforcement, external
connectivity, and tunnel protection from those files. Traffic on the same VLAN
does not cross an inter-VLAN router firewall. A declared segment without a
cabled link is not a reachable host. Do not treat future plans as deployed
configuration.

The attack scenario catalog is an index of families and existing lab tools.
It is not evidence that an exploit succeeds or that a bundled attacker, bot,
agent, or command server must hold that role in another campaign. Consider
plausible alternative compromised nodes and command infrastructure. For each
step, identify its prerequisite and whether the declared topology and firewall
permit, block, or leave the path uncertain.

Keep two evidence classes separate:

1. SDK security findings: vulnerabilities supported by repository evidence.
2. Theoretical network scenarios: plausible attack paths for defensive review,
   labelled as hypotheses even when a configuration supports a step.

Do not present a theoretical path as a confirmed vulnerability. Give CVE IDs
only when the affected product and version are supported by the selected files
and an authoritative advisory supports the match; otherwise use an empty CVE
list. Do not inspect Docker application code to infer unrecorded deployment
properties. Describe uncertain reachability as unknown.
