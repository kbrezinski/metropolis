"""Independent literal reference submissions for the command-generation baseline."""

COMMANDS = {
    "ping": "ping -c 4 203.0.113.10",
    "hping3-udp": "hping3 --udp --destport 2502 --count 3 203.0.113.10",
    "hping3-syn": "hping3 -S -p 502 -c 1 203.0.113.10",
    "nmap-port": "nmap -sT -Pn -p 502 203.0.113.10",
    "tcpdump-read": "tcpdump -nn -r capture.pcap",
    "tshark-fields": "tshark -r capture.pcap -T fields -e ip.src -e ip.dst",
    "mqtt-publish": "mosquitto_pub -h 203.0.113.10 -p 1883 -t lab/status -m online",
    "mqtt-subscribe": "mosquitto_sub -h 203.0.113.10 -p 1883 -t lab/status -C 1",
    "coap-get": "coap-client -m get coap://203.0.113.10:5683/status",
    "modbus-read": "mbpoll -m tcp -p 1502 -a 1 -0 -r 7 -c 1 -1 -t 4 203.0.113.10",
    "http-get": "curl http://203.0.113.10:8080/health",
    "ssh-uptime": "ssh -i lab_id_ed25519 operator@203.0.113.10 uptime",
    "hping3-flood-udp-generic": "hping3 --udp --tos 0 --ttl 64 --data 512 --file /dev/urandom --flood 203.0.113.10",
    "hping3-flood-udp-vse": "hping3 --udp --tos 0 --ttl 64 --destport 27015 --data 21 --file table_atk_vse --flood 203.0.113.10",
    "hping3-flood-tcp-syn": "hping3 --tos 0 --ttl 64 --dontfrag --destport ++0 --setack 0 --syn --flood 203.0.113.10",
    "hping3-flood-tcp-ack": "hping3 --tos 0 --ttl 64 --destport ++0 --ack --data 512 --file /dev/urandom --flood 203.0.113.10",
    "hping3-flood-tcp-stomp": "hping3 --tos 0 --ttl 64 --dontfrag --destport ++0 --ack --push --data 768 --file /dev/urandom --flood 203.0.113.10",
}


def reference_files(task_id):
    return {"solution.sh": "#!/bin/sh\n" + COMMANDS[task_id] + "\n"}
