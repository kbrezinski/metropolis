#!/bin/vbash
# Metropolis water-treatment OT testbed: DMZ, enterprise, and attack-test edge.
# This standalone script configures routing only. The GNS3 --configure-routers
# path adds generated zone rules from topology/firewall-policy.yaml.
#
# GNS3 interface map:
#   eth0 -> OT core router, transit 10.20.254.4/30
#   eth1 -> DMZ switch, 10.20.40.0/24
#   eth2 -> enterprise switch, 10.30.10.0/24
#   eth3 -> isolated attack-test switch, 10.99.10.0/24
# OSPF advertises enterprise and attack-test prefixes passively. Generated
# firewall rules block new attack-lab flows unless a run selects an exception.

source /opt/vyatta/etc/functions/script-template

configure

set interfaces ethernet eth0 description 'Transit to OT core'
set interfaces ethernet eth0 address '10.20.254.6/30'
set interfaces ethernet eth0 ip ospf network 'point-to-point'
set interfaces ethernet eth1 description 'OT DMZ network'
set interfaces ethernet eth1 address '10.20.40.1/24'
set interfaces ethernet eth2 description 'Enterprise network'
set interfaces ethernet eth2 address '10.30.10.1/24'
set interfaces ethernet eth3 description 'Isolated attack-test network'
set interfaces ethernet eth3 address '10.99.10.1/24'

set protocols ospf parameters router-id '10.20.254.6'
set protocols ospf log-adjacency-changes
set protocols ospf passive-interface default
set protocols ospf passive-interface-exclude 'eth0'
set protocols ospf area 0 network '10.20.254.4/30'
set protocols ospf area 0 network '10.20.40.0/24'
set protocols ospf area 0 network '10.30.10.0/24'
set protocols ospf area 0 network '10.99.10.0/24'

set system host-name 'MET-OT-DMZ'
set system syslog host 10.20.30.11 facility all level info
set system console device ttyS0 speed '115200'

commit
save
exit
