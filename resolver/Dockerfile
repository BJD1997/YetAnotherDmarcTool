# resolver/Dockerfile
# Custom Unbound image for the Azure Container Apps deployment, where the
# DNSSEC-validating resolver runs as a SIDECAR inside the api/worker apps rather
# than as its own service. ACA ingress only supports HTTP/TCP (not UDP/53), so a
# standalone resolver app would be unreachable for DNS; a sidecar shares the
# app's network namespace, so api/worker query it over 127.0.0.1 (see
# DNS_RESOLVER_HOST=127.0.0.1 in the Bicep). This bakes the config that
# docker-compose.yml bind-mounts (compose keeps using mvance/unbound:latest with
# those mounts and is unaffected by this file).
FROM mvance/unbound:1.22.0

COPY resolver/forward-records.conf /opt/unbound/etc/unbound/forward-records.conf
COPY resolver/overrides.conf /opt/unbound/etc/unbound/unbound.conf.d/overrides.conf

# Sidecar model: api/worker reach this over loopback, so allow it explicitly
# regardless of the base image's default access-control.
RUN printf '\n    access-control: 127.0.0.0/8 allow\n    access-control: ::1 allow\n' \
    >> /opt/unbound/etc/unbound/unbound.conf.d/overrides.conf
