# Retrieved from Phenomite's AMP-Research project for Metropolis.
# https://github.com/Phenomite/AMP-Research (MIT license).

FROM ubuntu:focal

RUN apt update && DEBIAN_FRONTEND=noninteractive apt install -y \
    git \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/Phenomite/AMP-Research.git

WORKDIR /opt

RUN mkdir ipsec &&\
    cp '/AMP-Research/Port 500 - IPSec/ipsec.c' ipsec/ &&\
    cp /AMP-Research/LICENSE ipsec/ &&\
    cd ipsec &&\
    echo '8609492e21abfbf228ab2c6aa785fd7a8a4984d5e5d8f10d17960b4354267198  ipsec.c' | sha256sum -c - &&\
    echo '854d2de5560aecc1cf5f3e73fa066ac1189a54bc3d6500ae190501d871eeb50d  LICENSE' | sha256sum -c - &&\
    cd /opt &&\
    git -C /AMP-Research rev-parse HEAD > ipsec/UPSTREAM_COMMIT &&\
    rm -r /AMP-Research

RUN cd ./ipsec &&\
    gcc -pthread -O2 -o amplification ipsec.c

CMD ["/bin/bash"]
