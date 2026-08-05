ARG JUPYTERHUB_VERSION=5.4.3
FROM quay.io/jupyterhub/jupyterhub:$JUPYTERHUB_VERSION

RUN python3 -m pip install --no-cache \
    dockerspawner \
    oauthenticator \
    pydantic_settings \ 
    jinjaMarkdown

COPY logo /srv/jupyterhub/logo
COPY templates /srv/jupyterhub/templates
COPY jupyterhub_config.py /srv/jupyterhub/jupyterhub_config.py

# CMD ["jupyterhub", "-f", "/srv/jupyterhub/jupyterhub_config.py"]