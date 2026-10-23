# Retrieved from Phenomite's AMP-Research project for Metropolis.
# https://github.com/Phenomite/AMP-Research (MIT license).

FROM ubuntu:focal

RUN apt update && DEBIAN_FRONTEND=noninteractive apt install -y \
    git \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/Phenomite/AMP-Research.git

WORKDIR /opt

RUN mkdir bfd &&\
    cp '/AMP-Research/Port 3784 - BFD/bfd.c' bfd/ &&\
    cp /AMP-Research/LICENSE bfd/ &&\
    cd bfd &&\
    echo '32f1329013cc1cb16b21c69aa1fbc21aa324296749aa709e7cdbee13f8c813ec  bfd.c' | sha256sum -c - &&\
    echo '854d2de5560aecc1cf5f3e73fa066ac1189a54bc3d6500ae190501d871eeb50d  LICENSE' | sha256sum -c - &&\
    cd /opt &&\
    git -C /AMP-Research rev-parse HEAD > bfd/UPSTREAM_COMMIT &&\
    rm -r /AMP-Research

RUN cd ./bfd &&\
    gcc -pthread -O2 -o amplification bfd.c

CMD ["/bin/bash"]
