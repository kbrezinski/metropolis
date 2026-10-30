#!/bin/vbash
# Metropolis water-treatment OT testbed: WAN edge firewall and IPsec hub.
# The shared GNS3 WAN carries IKE/ESP; OSPF and operational traffic use VTIs.
#
# GNS3 interface map:
#   eth0 -> OT core router, transit 10.20.254.8/30
#   eth1 -> simulated WAN switch, 172.31.255.0/24
#   vti10 -> raw-water IPsec overlay, 10.20.254.12/30 (logical, no GNS3 cable)
#   vti20 -> reservoir IPsec overlay, 10.20.254.16/30 (logical, no GNS3 cable)
# The GNS3 --configure-routers path adds generated firewall zone rules from
# topology/firewall-policy.yaml, including peer-scoped WAN IKE/ESP rules.

source /opt/vyatta/etc/functions/script-template

: "${METROPOLIS_IPSEC_RAW_WATER_PSK:?Set a unique raw-water peer secret}"
: "${METROPOLIS_IPSEC_RESERVOIR_PSK:?Set a unique reservoir peer secret}"

configure

set interfaces ethernet eth0 description 'Transit to OT core'
set interfaces ethernet eth0 address '10.20.254.10/30'
set interfaces ethernet eth0 ip ospf network 'point-to-point'
set interfaces ethernet eth0 ip ospf bfd
set interfaces ethernet eth1 description 'Untrusted IPsec underlay - no operational routing'
set interfaces ethernet eth1 address '172.31.255.1/24'

# Remove only the previous Metropolis plaintext WAN routing settings.
delete interfaces ethernet eth1 ip ospf bfd
delete protocols ospf passive-interface-exclude 'eth1'
delete protocols ospf area 0 network '172.31.255.0/24'
delete protocols bfd peer '172.31.255.11'
delete protocols bfd peer '172.31.255.21'

set interfaces vti vti10 description 'IPsec to raw-water station'
set interfaces vti vti10 address '10.20.254.13/30'
set interfaces vti vti10 mtu '1400'
set interfaces vti vti10 ip ospf network 'point-to-point'
set interfaces vti vti10 ip ospf bfd
set interfaces vti vti20 description 'IPsec to reservoir station'
set interfaces vti vti20 address '10.20.254.17/30'
set interfaces vti vti20 mtu '1400'
set interfaces vti vti20 ip ospf network 'point-to-point'
set interfaces vti vti20 ip ospf bfd

set protocols ospf parameters router-id '10.20.254.10'
set protocols ospf log-adjacency-changes
set protocols ospf passive-interface default
set protocols ospf passive-interface-exclude 'eth0'
set protocols ospf passive-interface-exclude 'vti10'
set protocols ospf passive-interface-exclude 'vti20'
set protocols ospf area 0 network '10.20.254.8/30'
set protocols ospf area 0 network '10.20.254.12/30'
set protocols ospf area 0 network '10.20.254.16/30'
set protocols bfd peer '10.20.254.9'
set protocols bfd peer '10.20.254.14'
set protocols bfd peer '10.20.254.18'

set vpn ipsec options disable-route-autoinstall
set vpn ipsec ipsec-interfaces interface 'eth1'
set vpn ipsec ike-group MET_IKE key-exchange 'ikev2'
set vpn ipsec ike-group MET_IKE lifetime '10800'
set vpn ipsec ike-group MET_IKE ikev2-reauth 'no'
set vpn ipsec ike-group MET_IKE mobike 'disable'
set vpn ipsec ike-group MET_IKE dead-peer-detection action 'hold'
set vpn ipsec ike-group MET_IKE dead-peer-detection interval '30'
set vpn ipsec ike-group MET_IKE dead-peer-detection timeout '120'
set vpn ipsec ike-group MET_IKE proposal 1 dh-group '19'
set vpn ipsec ike-group MET_IKE proposal 1 encryption 'aes256gcm128'
set vpn ipsec ike-group MET_IKE proposal 1 hash 'sha256'
set vpn ipsec esp-group MET_ESP compression 'disable'
set vpn ipsec esp-group MET_ESP lifetime '3600'
set vpn ipsec esp-group MET_ESP mode 'tunnel'
set vpn ipsec esp-group MET_ESP pfs 'dh-group19'
set vpn ipsec esp-group MET_ESP proposal 1 encryption 'aes256gcm128'
set vpn ipsec esp-group MET_ESP proposal 1 hash 'sha256'

set vpn ipsec site-to-site peer '172.31.255.11' authentication id '172.31.255.1'
set vpn ipsec site-to-site peer '172.31.255.11' authentication remote-id '172.31.255.11'
set vpn ipsec site-to-site peer '172.31.255.11' authentication mode 'pre-shared-secret'
set vpn ipsec site-to-site peer '172.31.255.11' authentication pre-shared-secret "${METROPOLIS_IPSEC_RAW_WATER_PSK}"
set vpn ipsec site-to-site peer '172.31.255.11' connection-type 'respond'
set vpn ipsec site-to-site peer '172.31.255.11' ike-group 'MET_IKE'
set vpn ipsec site-to-site peer '172.31.255.11' local-address '172.31.255.1'
set vpn ipsec site-to-site peer '172.31.255.11' vti bind 'vti10'
set vpn ipsec site-to-site peer '172.31.255.11' vti esp-group 'MET_ESP'

set vpn ipsec site-to-site peer '172.31.255.21' authentication id '172.31.255.1'
set vpn ipsec site-to-site peer '172.31.255.21' authentication remote-id '172.31.255.21'
set vpn ipsec site-to-site peer '172.31.255.21' authentication mode 'pre-shared-secret'
set vpn ipsec site-to-site peer '172.31.255.21' authentication pre-shared-secret "${METROPOLIS_IPSEC_RESERVOIR_PSK}"
set vpn ipsec site-to-site peer '172.31.255.21' connection-type 'respond'
set vpn ipsec site-to-site peer '172.31.255.21' ike-group 'MET_IKE'
set vpn ipsec site-to-site peer '172.31.255.21' local-address '172.31.255.1'
set vpn ipsec site-to-site peer '172.31.255.21' vti bind 'vti20'
set vpn ipsec site-to-site peer '172.31.255.21' vti esp-group 'MET_ESP'

set service snmp community metropolis_ro authorization ro
set service snmp community metropolis_ro client 10.20.21.20

set system host-name 'MET-OT-WAN'
set system syslog host 10.20.30.11 facility all level info
set system console device ttyS0 speed '115200'

commit
save
exit
