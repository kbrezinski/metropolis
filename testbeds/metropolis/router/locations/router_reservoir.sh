#!/bin/vbash
# Metropolis water-treatment OT testbed: reservoir and booster station router.
#
# GNS3 interface map:
#   eth0 -> reservoir station switch, 10.20.14.0/24
#   eth1 -> simulated WAN switch, 172.31.255.0/24
#   vti20 -> WAN hub IPsec overlay, 10.20.254.16/30 (logical, no GNS3 cable)
# The GNS3 --configure-routers path adds generated firewall zone rules from
# topology/firewall-policy.yaml; this script configures routing and SNMP.

source /opt/vyatta/etc/functions/script-template

: "${METROPOLIS_IPSEC_RESERVOIR_PSK:?Set the matching reservoir peer secret}"

configure

set interfaces ethernet eth0 description 'Reservoir station LAN'
set interfaces ethernet eth0 address '10.20.14.1/24'
set interfaces ethernet eth1 description 'Untrusted IPsec underlay - no operational routing'
set interfaces ethernet eth1 address '172.31.255.21/24'

delete interfaces ethernet eth1 ip ospf bfd
delete protocols ospf passive-interface-exclude 'eth1'
delete protocols ospf area 0 network '172.31.255.0/24'
delete protocols bfd peer '172.31.255.1'
delete protocols bfd peer '172.31.255.11'

set interfaces vti vti20 description 'IPsec to WAN hub'
set interfaces vti vti20 address '10.20.254.18/30'
set interfaces vti vti20 mtu '1400'
set interfaces vti vti20 ip ospf network 'point-to-point'
set interfaces vti vti20 ip ospf bfd

set protocols ospf parameters router-id '172.31.255.21'
set protocols ospf log-adjacency-changes
set protocols ospf passive-interface default
set protocols ospf passive-interface-exclude 'vti20'
set protocols ospf area 0 network '10.20.254.16/30'
set protocols ospf area 0 network '10.20.14.0/24'
set protocols bfd peer '10.20.254.17'

set vpn ipsec options disable-route-autoinstall
set vpn ipsec ipsec-interfaces interface 'eth1'
set vpn ipsec ike-group MET_IKE key-exchange 'ikev2'
set vpn ipsec ike-group MET_IKE lifetime '10800'
set vpn ipsec ike-group MET_IKE ikev2-reauth 'no'
set vpn ipsec ike-group MET_IKE mobike 'disable'
set vpn ipsec ike-group MET_IKE dead-peer-detection action 'restart'
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

set vpn ipsec site-to-site peer '172.31.255.1' authentication id '172.31.255.21'
set vpn ipsec site-to-site peer '172.31.255.1' authentication remote-id '172.31.255.1'
set vpn ipsec site-to-site peer '172.31.255.1' authentication mode 'pre-shared-secret'
set vpn ipsec site-to-site peer '172.31.255.1' authentication pre-shared-secret "${METROPOLIS_IPSEC_RESERVOIR_PSK}"
set vpn ipsec site-to-site peer '172.31.255.1' connection-type 'initiate'
set vpn ipsec site-to-site peer '172.31.255.1' ike-group 'MET_IKE'
set vpn ipsec site-to-site peer '172.31.255.1' local-address '172.31.255.21'
set vpn ipsec site-to-site peer '172.31.255.1' vti bind 'vti20'
set vpn ipsec site-to-site peer '172.31.255.1' vti esp-group 'MET_ESP'

set service snmp community metropolis_ro authorization ro
set service snmp community metropolis_ro client 10.20.21.20

set system host-name 'MET-RESERVOIR'
set system syslog host 10.20.30.11 facility all level info
set system console device ttyS0 speed '115200'

commit
save
exit
