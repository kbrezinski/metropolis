# Retrieved from Phenomite's AMP-Research project for Metropolis.
# https://github.com/Phenomite/AMP-Research (MIT license).

FROM ubuntu:focal

RUN apt update && DEBIAN_FRONTEND=noninteractive apt install -y \
    git \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/Phenomite/AMP-Research.git

WORKDIR /opt

RUN mkdir ssdp &&\
    cp '/AMP-Research/Port 1900 - SSDP (refined payload)/ssdp.c' ssdp/ &&\
    cp /AMP-Research/LICENSE ssdp/ &&\
    cd ssdp &&\
    echo '58406cee4096a335c34a8d1140210029293ca8180e60d6c81ba1fe0797e5ff19  ssdp.c' | sha256sum -c - &&\
    echo '854d2de5560aecc1cf5f3e73fa066ac1189a54bc3d6500ae190501d871eeb50d  LICENSE' | sha256sum -c - &&\
    cd /opt &&\
    git -C /AMP-Research rev-parse HEAD > ssdp/UPSTREAM_COMMIT &&\
    rm -r /AMP-Research

RUN cd ./ssdp &&\
    gcc -pthread -O2 -o amplification ssdp.c

CMD ["/bin/bash"]
