"""Independent literal reference submissions for the command-generation baseline."""

COMMANDS = {
    "ping": "ping -c 4 192.168.1.10",
    "hping3-udp": "hping3 --udp --destport 2502 --count 3 192.168.1.10",
    "hping3-syn": "hping3 -S -p 502 -c 1 192.168.1.10",
    "nmap-port": "nmap -sT -Pn -p 502 192.168.1.10",
    "tcpdump-read": "tcpdump -nn -r capture.pcap",
    "tshark-fields": "tshark -r capture.pcap -T fields -e ip.src -e ip.dst",
    "mqtt-publish": "mosquitto_pub -h 192.168.1.10 -p 1883 -t site/status -m online",
    "mqtt-subscribe": "mosquitto_sub -h 192.168.1.10 -p 1883 -t site/status -C 1",
    "coap-get": "coap-client -m get coap://192.168.1.10:5683/status",
    "modbus-read": "mbpoll -m tcp -p 1502 -a 1 -0 -r 7 -c 1 -1 -t 4 192.168.1.10",
    "http-get": "curl http://192.168.1.10:8080/health",
    "ssh-uptime": "ssh -i operator_id_ed25519 operator@192.168.1.10 uptime",
}


def reference_files(task_id):
    return {"solution.sh": "#!/bin/sh\n" + COMMANDS[task_id] + "\n"}
