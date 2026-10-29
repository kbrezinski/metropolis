FROM python:3.12.12-slim-bookworm

# Debian snapshot pins native fixture tools as well as their transitive packages.
RUN rm -f /etc/apt/sources.list.d/debian.sources \
    && printf 'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20251001T000000Z bookworm main\n' > /etc/apt/sources.list \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && apt-get install -y --no-install-recommends hping3 strace util-linux \
    && chmod u-s /usr/sbin/hping3 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /workspace
CMD ["sleep", "infinity"]
