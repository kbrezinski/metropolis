# Retrieved from Phenomite's AMP-Research project for Metropolis.
# https://github.com/Phenomite/AMP-Research (MIT license).

FROM ubuntu:focal

RUN apt update && DEBIAN_FRONTEND=noninteractive apt install -y \
    git \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/Phenomite/AMP-Research.git

WORKDIR /opt

RUN mkdir dns &&\
    cp '/AMP-Research/Miscellaneous (not worth individual mention)/dns.c' dns/ &&\
    cp /AMP-Research/LICENSE dns/ &&\
    cd dns &&\
    echo '14c6a433ed6b01267ce4475c87d6d512a3d1990855fa81f6ee322bdb82075907  dns.c' | sha256sum -c - &&\
    echo '854d2de5560aecc1cf5f3e73fa066ac1189a54bc3d6500ae190501d871eeb50d  LICENSE' | sha256sum -c - &&\
    cd /opt &&\
    git -C /AMP-Research rev-parse HEAD > dns/UPSTREAM_COMMIT &&\
    rm -r /AMP-Research

RUN cd ./dns &&\
    gcc -pthread -O2 -o amplification dns.c

CMD ["/bin/bash"]
