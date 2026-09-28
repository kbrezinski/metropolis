#!/bin/vbash
# Metropolis water-treatment OT testbed: DMZ, enterprise, and attack-test edge.
# This draft configures routing only; it does not apply firewall policy.
#
# GNS3 interface map:
#   eth0 -> OT core router, transit 10.20.254.4/30
#   eth1 -> DMZ switch, 10.20.40.0/24
#   eth2 -> enterprise switch, 10.30.10.0/24
#   eth3 -> isolated attack-test switch, 10.99.10.0/24
# TODO(Metropolis): Define and apply deny-by-default policies for enterprise,
# DMZ, OT, and attack-test traffic. Specify approved services and test cases
# before allowing any path from 10.99.10.0/24 into OT.

source /opt/vyatta/etc/functions/script-template

configure

set interfaces ethernet eth0 description 'Transit to OT core'
set interfaces ethernet eth0 address '10.20.254.6/30'
set interfaces ethernet eth1 description 'OT DMZ network'
set interfaces ethernet eth1 address '10.20.40.1/24'
set interfaces ethernet eth2 description 'Enterprise network'
set interfaces ethernet eth2 address '10.30.10.1/24'
set interfaces ethernet eth3 description 'Isolated attack-test network'
set interfaces ethernet eth3 address '10.99.10.1/24'

set protocols static route 10.20.0.0/16 next-hop '10.20.254.5'

set system host-name 'MET-OT-DMZ'
set system console device ttyS0 speed '115200'

commit
save
exit
