# Retrieved from Phenomite's AMP-Research project for Metropolis.
# https://github.com/Phenomite/AMP-Research (MIT license).

FROM ubuntu:focal

RUN apt update && DEBIAN_FRONTEND=noninteractive apt install -y \
    git \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/Phenomite/AMP-Research.git

WORKDIR /opt

RUN mkdir ntp &&\
    cp '/AMP-Research/Miscellaneous (not worth individual mention)/ntp.c' ntp/ &&\
    cp /AMP-Research/LICENSE ntp/ &&\
    cd ntp &&\
    echo '9e9c6008e503cd57b1c1c1cbd4fb9c9652665d546426a02a95d2b528a87f1ba8  ntp.c' | sha256sum -c - &&\
    echo '854d2de5560aecc1cf5f3e73fa066ac1189a54bc3d6500ae190501d871eeb50d  LICENSE' | sha256sum -c - &&\
    cd /opt &&\
    git -C /AMP-Research rev-parse HEAD > ntp/UPSTREAM_COMMIT &&\
    rm -r /AMP-Research

RUN cd ./ntp &&\
    gcc -pthread -O2 -o amplification ntp.c

CMD ["/bin/bash"]
