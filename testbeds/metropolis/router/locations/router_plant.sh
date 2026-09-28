#!/bin/vbash
# Metropolis water-treatment OT testbed: main treatment-plant router.
# Uses the VyOS 1.3 appliance style inherited from the Gotham foundation.
#
# GNS3 interface map:
#   eth0 -> OT core router, transit 10.20.254.0/30
#   eth1 -> plant aggregation switch, 802.1Q trunk
# The plant switch trunk must carry VLANs 10-13 and 50-53.
# VLAN IDs match the third octet of their subnets.
# TODO(Metropolis): Add plant-zone firewall policy after documenting the
# required HMI-to-PLC, SCADA-to-PLC, engineering, and monitoring flows.

source /opt/vyatta/etc/functions/script-template

configure

set interfaces ethernet eth0 description 'Transit to OT core'
set interfaces ethernet eth0 address '10.20.254.2/30'

set interfaces ethernet eth1 description '802.1Q trunk to plant aggregation switch'
set interfaces ethernet eth1 vif 10 description 'Intake PLC network'
set interfaces ethernet eth1 vif 10 address '10.20.10.1/24'
set interfaces ethernet eth1 vif 11 description 'Dosing and clarification PLC network'
set interfaces ethernet eth1 vif 11 address '10.20.11.1/24'
set interfaces ethernet eth1 vif 12 description 'Filtration PLC network'
set interfaces ethernet eth1 vif 12 address '10.20.12.1/24'
set interfaces ethernet eth1 vif 13 description 'Disinfection and storage PLC network'
set interfaces ethernet eth1 vif 13 address '10.20.13.1/24'
set interfaces ethernet eth1 vif 50 description 'Intake HMI network'
set interfaces ethernet eth1 vif 50 address '10.20.50.1/24'
set interfaces ethernet eth1 vif 51 description 'Dosing and clarification HMI network'
set interfaces ethernet eth1 vif 51 address '10.20.51.1/24'
set interfaces ethernet eth1 vif 52 description 'Filtration HMI network'
set interfaces ethernet eth1 vif 52 address '10.20.52.1/24'
set interfaces ethernet eth1 vif 53 description 'Disinfection and storage HMI network'
set interfaces ethernet eth1 vif 53 address '10.20.53.1/24'

set protocols static route 0.0.0.0/0 next-hop '10.20.254.1'

set system host-name 'MET-PLANT'
set system console device ttyS0 speed '115200'

commit
save
exit
