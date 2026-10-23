# Retrieved from Phenomite's AMP-Research project for Metropolis.
# https://github.com/Phenomite/AMP-Research (MIT license).

FROM ubuntu:focal

RUN apt update && DEBIAN_FRONTEND=noninteractive apt install -y \
    git \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/Phenomite/AMP-Research.git

WORKDIR /opt

RUN mkdir rdp &&\
    cp '/AMP-Research/Port 3389 - RDP/rdpeudp.c' rdp/ &&\
    cp /AMP-Research/LICENSE rdp/ &&\
    cd rdp &&\
    echo 'af2e84dbb2af8aee60ec8c85e0307ee36a33a9fd328015716152874f64fa168c  rdpeudp.c' | sha256sum -c - &&\
    echo '854d2de5560aecc1cf5f3e73fa066ac1189a54bc3d6500ae190501d871eeb50d  LICENSE' | sha256sum -c - &&\
    cd /opt &&\
    git -C /AMP-Research rev-parse HEAD > rdp/UPSTREAM_COMMIT &&\
    rm -r /AMP-Research

RUN cd ./rdp &&\
    gcc -pthread -O2 -o amplification rdpeudp.c

CMD ["/bin/bash"]
