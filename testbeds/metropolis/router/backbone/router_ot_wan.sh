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
# TODO(Metropolis): Add WAN input/forward firewall policy and document the
# permitted SCADA, MQTT, time, logging, and management flows.

source /opt/vyatta/etc/functions/script-template

configure

set interfaces ethernet eth0 description 'Transit to OT core'
set interfaces ethernet eth0 address '10.20.254.10/30'
set interfaces ethernet eth1 description 'Simulated WAN underlay'
set interfaces ethernet eth1 address '172.31.255.1/24'

set protocols static route 10.20.14.0/24 next-hop '172.31.255.21'
set protocols static route 10.20.15.0/24 next-hop '172.31.255.11'

set system host-name 'MET-OT-WAN'
set system console device ttyS0 speed '115200'

commit
save
exit
