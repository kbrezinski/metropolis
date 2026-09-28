FROM eclipse-mosquitto:2

RUN apk add --no-cache iproute2
COPY testbeds/metropolis/devices/services/mqtt_broker/mosquitto.conf /mosquitto/config/mosquitto.conf
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint

ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["/usr/sbin/mosquitto", "-c", "/mosquitto/config/mosquitto.conf"]
EXPOSE 1883/tcp
