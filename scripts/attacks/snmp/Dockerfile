# Retrieved from Phenomite's AMP-Research project for Metropolis.
# https://github.com/Phenomite/AMP-Research (MIT license).

FROM ubuntu:focal

RUN apt update && DEBIAN_FRONTEND=noninteractive apt install -y \
    git \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/Phenomite/AMP-Research.git

WORKDIR /opt

RUN mkdir snmp &&\
    cp '/AMP-Research/Port 161 - SNMP (refined payload)/snmp.c' snmp/ &&\
    cp /AMP-Research/LICENSE snmp/ &&\
    cd snmp &&\
    echo 'd362f8008e31671315420143a7c2c2c758cf42c0476a92fbf8071c97eed6b262  snmp.c' | sha256sum -c - &&\
    echo '854d2de5560aecc1cf5f3e73fa066ac1189a54bc3d6500ae190501d871eeb50d  LICENSE' | sha256sum -c - &&\
    cd /opt &&\
    git -C /AMP-Research rev-parse HEAD > snmp/UPSTREAM_COMMIT &&\
    rm -r /AMP-Research

RUN cd ./snmp &&\
    gcc -pthread -O2 -o amplification snmp.c

CMD ["/bin/bash"]
