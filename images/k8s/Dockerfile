# https://hub.jupyter.org/helm-chart/

ARG Z2JH_VERSION=4.4.2
FROM quay.io/jupyterhub/k8s-hub:$Z2JH_VERSION

RUN python3 -m pip install --no-cache-dir jinjaMarkdown

COPY logo /usr/local/share/north/logo
COPY templates /usr/local/share/north/templates
COPY jupyterhub_config_k8s.py /usr/local/etc/jupyterhub/jupyterhub_config.d/north.py