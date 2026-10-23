#!/bin/vbash
# Metropolis water-treatment OT testbed: OT routing hub for the main plant.
#
# GNS3 interface map:
#   eth0 -> plant router, transit 10.20.254.0/30
#   eth1 -> OT/DMZ boundary router, transit 10.20.254.4/30
#   eth2 -> WAN hub router, transit 10.20.254.8/30
#   eth3 -> OT operations aggregation switch, 802.1Q trunk
# The operations switch trunk must carry VLANs 20-23 and 30.
# The GNS3 --configure-routers path adds generated firewall zone rules from
# topology/firewall-policy.yaml; this script configures routing and SNMP.

source /opt/vyatta/etc/functions/script-template

configure

set interfaces ethernet eth0 description 'Transit to plant router'
set interfaces ethernet eth0 address '10.20.254.1/30'
set interfaces ethernet eth0 ip ospf network 'point-to-point'
set interfaces ethernet eth0 ip ospf bfd
set interfaces ethernet eth1 description 'Transit to OT DMZ boundary router'
set interfaces ethernet eth1 address '10.20.254.5/30'
set interfaces ethernet eth1 ip ospf network 'point-to-point'
set interfaces ethernet eth1 ip ospf bfd
set interfaces ethernet eth2 description 'Transit to WAN hub router'
set interfaces ethernet eth2 address '10.20.254.9/30'
set interfaces ethernet eth2 ip ospf network 'point-to-point'
set interfaces ethernet eth2 ip ospf bfd

set interfaces ethernet eth3 description '802.1Q trunk to OT operations switch'
set interfaces ethernet eth3 vif 20 description 'SCADA network'
set interfaces ethernet eth3 vif 20 address '10.20.20.1/24'
set interfaces ethernet eth3 vif 21 description 'Engineering workstation network'
set interfaces ethernet eth3 vif 21 address '10.20.21.1/24'
set interfaces ethernet eth3 vif 22 description 'Historian network'
set interfaces ethernet eth3 vif 22 address '10.20.22.1/24'
set interfaces ethernet eth3 vif 23 description 'MQTT broker network'
set interfaces ethernet eth3 vif 23 address '10.20.23.1/24'
set interfaces ethernet eth3 vif 30 description 'OT infrastructure services network'
set interfaces ethernet eth3 vif 30 address '10.20.30.1/24'

# OSPF advertises LANs without forming neighbors on device-facing VLANs.
set protocols ospf parameters router-id '10.20.254.1'
set protocols ospf log-adjacency-changes
set protocols ospf passive-interface default
set protocols ospf passive-interface-exclude 'eth0'
set protocols ospf passive-interface-exclude 'eth1'
set protocols ospf passive-interface-exclude 'eth2'
set protocols ospf area 0 network '10.20.254.0/30'
set protocols ospf area 0 network '10.20.254.4/30'
set protocols ospf area 0 network '10.20.254.8/30'
set protocols ospf area 0 network '10.20.20.0/24'
set protocols ospf area 0 network '10.20.21.0/24'
set protocols ospf area 0 network '10.20.22.0/24'
set protocols ospf area 0 network '10.20.23.0/24'
set protocols ospf area 0 network '10.20.30.0/24'
set protocols bfd peer '10.20.254.2'
set protocols bfd peer '10.20.254.6'
set protocols bfd peer '10.20.254.10'

set service snmp community metropolis_ro authorization ro
set service snmp community metropolis_ro client 10.20.21.20

set system host-name 'MET-OT-CORE'
set system syslog host 10.20.30.11 facility all level info
set system console device ttyS0 speed '115200'

commit
save
exit
