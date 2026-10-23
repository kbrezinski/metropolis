#!/bin/vbash
# Metropolis water-treatment OT testbed: raw-water lift station router.
#
# GNS3 interface map:
#   eth0 -> raw-water station switch, 10.20.15.0/24
#   eth1 -> simulated WAN switch, 172.31.255.0/24
# This is routed WAN connectivity, not an IPsec tunnel yet.
# TODO(Metropolis): Configure the matching IPsec peer/VTI to the WAN hub using
# overlay 10.20.254.12/30; do not put the peer secret in this file.
# The GNS3 --configure-routers path adds generated firewall zone rules from
# topology/firewall-policy.yaml; this script configures routing and SNMP.

source /opt/vyatta/etc/functions/script-template

configure

set interfaces ethernet eth0 description 'Raw-water station LAN'
set interfaces ethernet eth0 address '10.20.15.1/24'
set interfaces ethernet eth1 description 'Simulated WAN underlay'
set interfaces ethernet eth1 address '172.31.255.11/24'
set interfaces ethernet eth1 ip ospf bfd

set protocols ospf parameters router-id '172.31.255.11'
set protocols ospf log-adjacency-changes
set protocols ospf passive-interface default
set protocols ospf passive-interface-exclude 'eth1'
set protocols ospf area 0 network '172.31.255.0/24'
set protocols ospf area 0 network '10.20.15.0/24'
set protocols bfd peer '172.31.255.1'
set protocols bfd peer '172.31.255.21'

set service snmp community metropolis_ro authorization ro
set service snmp community metropolis_ro client 10.20.21.20

set system host-name 'MET-RAW-WATER'
set system syslog host 10.20.30.11 facility all level info
set system console device ttyS0 speed '115200'

commit
save
exit
