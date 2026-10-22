FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEVICE_NAME=MET-PLC-INTAKE-01 \
    DEVICE_ROLE=PLC \
    MODBUS_HOST=0.0.0.0 \
    MODBUS_PORT=502 \
    EIP_RPI_MS=100

RUN apt-get update \
    && apt-get install -y --no-install-recommends iproute2 hostname \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pymodbus==3.8.6
COPY testbeds/metropolis/devices/controllers/plc/modbus_controller.py /opt/metropolis/modbus_controller.py
COPY testbeds/metropolis/devices/controllers/plc/dosing_controller.py /opt/metropolis/dosing_controller.py
COPY testbeds/metropolis/devices/controllers/plc/filtration_controller.py /opt/metropolis/filtration_controller.py
COPY testbeds/metropolis/devices/controllers/plc/disinfection_controller.py /opt/metropolis/disinfection_controller.py
COPY testbeds/metropolis/devices/controllers/plc/remote_site_controller.py /opt/metropolis/remote_site_controller.py
COPY testbeds/metropolis/devices/controllers/plc/process_model.py /opt/metropolis/process_model.py
COPY testbeds/metropolis/devices/runtime/ethernet_ip.py /opt/metropolis/ethernet_ip.py
COPY testbeds/metropolis/devices/runtime/alarms.py /opt/metropolis/alarms.py
COPY testbeds/metropolis/devices/runtime/network-entrypoint.sh /usr/local/bin/metropolis-entrypoint
RUN chmod +x /usr/local/bin/metropolis-entrypoint

EXPOSE 502/tcp
ENTRYPOINT ["/usr/local/bin/metropolis-entrypoint"]
CMD ["python", "/opt/metropolis/modbus_controller.py"]
