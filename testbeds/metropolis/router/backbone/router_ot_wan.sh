#!/bin/vbash
# Metropolis water-treatment OT testbed: WAN hub for the remote stations.
# The 172.31.255.0/24 segment is a simulated, shared GNS3 WAN.
# This config does not create IPsec tunnels; the 10.20.254.12/30 and
# 10.20.254.16/30 VPN overlay subnets remain reserved.
#
# GNS3 interface map:
#   eth0 -> OT core router, transit 10.20.254.8/30
#   eth1 -> simulated WAN switch, 172.31.255.0/24
# TODO(Metropolis): Configure a route-based IPsec VTI to each remote router,
# bind each peer to its reserved overlay subnet, and set routes through the
# VTIs. Supply unique lab-only peer secrets outside this checked-in script.
# The GNS3 --configure-routers path adds generated firewall zone rules from
# topology/firewall-policy.yaml; this script configures routing and SNMP.

source /opt/vyatta/etc/functions/script-template

configure

set interfaces ethernet eth0 description 'Transit to OT core'
set interfaces ethernet eth0 address '10.20.254.10/30'
set interfaces ethernet eth0 ip ospf network 'point-to-point'
set interfaces ethernet eth0 ip ospf bfd
set interfaces ethernet eth1 description 'Simulated WAN underlay'
set interfaces ethernet eth1 address '172.31.255.1/24'
set interfaces ethernet eth1 ip ospf bfd

# The shared WAN stays a broadcast OSPF segment with hub/site neighbors.
set protocols ospf parameters router-id '10.20.254.10'
set protocols ospf log-adjacency-changes
set protocols ospf passive-interface default
set protocols ospf passive-interface-exclude 'eth0'
set protocols ospf passive-interface-exclude 'eth1'
set protocols ospf area 0 network '10.20.254.8/30'
set protocols ospf area 0 network '172.31.255.0/24'
set protocols bfd peer '10.20.254.9'
set protocols bfd peer '172.31.255.11'
set protocols bfd peer '172.31.255.21'

set service snmp community metropolis_ro authorization ro
set service snmp community metropolis_ro client 10.20.21.20

set system host-name 'MET-OT-WAN'
set system syslog host 10.20.30.11 facility all level info
set system console device ttyS0 speed '115200'

commit
save
exit
