#!/bin/vbash
# Metropolis water-treatment OT testbed: raw-water lift station router.
#
# GNS3 interface map:
#   eth0 -> raw-water station switch, 10.20.15.0/24
#   eth1 -> simulated WAN switch, 172.31.255.0/24
# This is routed WAN connectivity, not an IPsec tunnel yet.
# TODO(Metropolis): Configure the matching IPsec peer/VTI to the WAN hub using
# overlay 10.20.254.12/30; do not put the peer secret in this file.
# TODO(Metropolis): Add station firewall policy after defining RTU/PLC-to-SCADA,
# MQTT, time, logging, and remote-management flows.

source /opt/vyatta/etc/functions/script-template

configure

set interfaces ethernet eth0 description 'Raw-water station LAN'
set interfaces ethernet eth0 address '10.20.15.1/24'
set interfaces ethernet eth1 description 'Simulated WAN underlay'
set interfaces ethernet eth1 address '172.31.255.11/24'

set protocols static route 10.20.20.0/24 next-hop '172.31.255.1'
set protocols static route 10.20.23.0/24 next-hop '172.31.255.1'
set protocols static route 10.20.30.0/24 next-hop '172.31.255.1'

set system host-name 'MET-RAW-WATER'
set system console device ttyS0 speed '115200'

commit
save
exit
