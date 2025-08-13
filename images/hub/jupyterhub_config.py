# Configuration file for JupyterHub


# import asyncio
import copy
# import ipaddress
# import os
# import re
# import string
# import sys
# import warnings
# from functools import partial
from typing import Optional, Tuple, Type
# from urllib.parse import urlparse

import logging
import jupyterhub

from tornado.httputil import url_concat
from jupyterhub.utils import url_path_join
from urllib.parse import parse_qsl

from jinja2 import ChoiceLoader, Environment, FileSystemLoader, PackageLoader
# from jupyterhub.spawner import Spawner
# from jupyterhub.traitlets import Callable, Command
# from jupyterhub.utils import exponential_backoff, maybe_future
# from kubernetes_asyncio import client
# from kubernetes_asyncio.client.rest import ApiException
# from slugify import slugify
# from traitlets import (
#     Bool,
#     Dict,
#     Enum,
#     Integer,
#     List,
#     Unicode,
#     Union,
#     default,
#     observe,
#     validate,
# )
#
# from . import __version__
# from .clients import load_config, shared_client
# from .objects import (
#     make_namespace,
#     make_owner_reference,
#     make_pod,
#     make_pvc,
#     make_secret,
#     make_service,
# )
# from .reflector import ResourceReflector
# from .slugs import escape_slug, is_valid_label, multi_slug, safe_slug
# from .utils import recursive_format, recursive_update

import os
import yaml

from dockerspawner import DockerSpawner

c = get_config()  # noqa: F821


class NORTHSpawner(DockerSpawner):

    profile_list = None

    def _options_form_default(self):

        if not self.profile_list:
            return ""

        loader = FileSystemLoader('/srv/jupyterhub/templates')
        env = Environment(loader=loader)

        # jinja2's tojson sorts keys in dicts by default.
        env.policies["json.dumps_kwargs"] = {"sort_keys": False}

        profile_form_template = env.get_template("form.html")

        return profile_form_template.render(profile_list=self.profile_list)


    def options_from_form(self, formdata):
        """
        Called by jupyterhub when processing a request to spawn a server, where
        the user either have submitted a POST request via a form or submitted a
        GET request with query parameters.

        Args:
            formdata: user selection returned by the form

        Returns:
            user_options (dict): the selected profile in the user_options form,
                e.g. ``{"profile": "cpus-8"}``
        """
        options = {}
        self.log.info(formdata)

        profile_slug = formdata.get("profile", [None])[0]

        if profile_slug:
            options["profile"] = profile_slug

        self.log.info(f"Generating options: {options}")

        return options

    def load_user_options(self, options):
        self.log.info(f"load options: {options}")

        profile = self._get_profile(options["profile"])
        self.log.info(f"load profile: {profile}")

        image = profile.get("image")
        if image:
            self.log.info(f"Loading image {image}")
            self.image = image


    def _get_profile(self, slug: str):
        """
        Returns the profile from profile_list matching given slug, or the
        (first) default profile if slug is falsy.

        profile_list is required to have a default profile.

        Raises an error if no profile exists for the given slug.
        """

        for profile in self.profile_list:
            if profile['slug'] == slug:
                # return matching profile
                return profile

        raise ValueError(
            "No such profile: %s. Options include: %s"
            % (slug, ', '.join(p['slug'] for p in self.profile_list))
        )


# Loding the profile list from file
with open("profile_list.yaml") as stream:
    try:
        NORTHSpawner.profile_list = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        logging.warning(exc)



options_form_tpl = """
<label for="image">Image</label>
<input name="image" class="form-control" placeholder="the image to launch (default: {default_image})"></input>
"""


def get_options_form(spawner):
    return options_form_tpl.format(default_image=spawner.image)


# c.DockerSpawner.options_form = get_options_form



class CustomDockerSpawner(DockerSpawner):
    def options_from_form(self, formdata):
        options = {}
        image_form_list = formdata.get("image", [])
        if image_form_list and image_form_list[0]:
            options["image"] = image_form_list[0].strip()
            self.log.info(f"User selected image: {options['image']}")
        return options

    def load_user_options(self, options):
        image = options.get("image")
        if image:
            self.log.info(f"Loading image {image}")
            self.image = image



# We rely on environment variables to configure JupyterHub so that we
# avoid having to rebuild the JupyterHub container every time we change a
# configuration parameter.

# Spawn single-user servers as Docker containers
# c.JupyterHub.spawner_class = "dockerspawner.DockerSpawner"
# c.JupyterHub.spawner_class = CustomDockerSpawner
c.JupyterHub.spawner_class = NORTHSpawner

# Spawn containers from this image
# c.DockerSpawner.image = os.environ["DOCKER_NOTEBOOK_IMAGE"]

# For debugging arguments passed to spawned containers
c.DockerSpawner.debug = True

# User containers will access hub by container name on the Docker network
# c.JupyterHub.hub_ip = "nomad_north"
# c.JupyterHub.hub_port = 8080

c.JupyterHub.port = 9000

# Connect containers to this Docker network
network_name = os.environ["DOCKER_NETWORK_NAME"]
c.DockerSpawner.use_internal_ip = True
c.DockerSpawner.network_name = network_name

# Explicitly set notebook directory because we'll be mounting a volume to it.
# Most `jupyter/docker-stacks` *-notebook images run the Notebook server as
# user `jovyan`, and set the notebook directory to `/home/jovyan/work`.
# We follow the same convention.
# notebook_dir = os.environ.get("DOCKER_NOTEBOOK_DIR", )
# c.DockerSpawner.notebook_dir = notebook_dir

# Remove containers once they are stopped
c.DockerSpawner.remove = True

# Mount the real user's Docker volume on the host to the notebook user's
# notebook directory in the container
c.DockerSpawner.volumes = {"jupyterhub-user-{username}": "/home/jovyan/work"}

# Persist hub data on volume mounted inside container
c.JupyterHub.cookie_secret_file = "/data/jupyterhub_cookie_secret"
c.JupyterHub.db_url = "sqlite:////data/jupyterhub.sqlite"




#
# c.DockerSpawner.allowed_images = {
#     "tutorial-query-nomad-archive": "gitlab-registry.mpcdf.mpg.de/nomad-lab/ai-toolkit/tutorial-query-nomad-archive:refatoring",
#     "tutorial-dos-similarity-search": "gitlab-registry.mpcdf.mpg.de/nomad-lab/ai-toolkit/tutorial-dos-similarity-search:updates",
# }


# Authentication
c.JupyterHub.authenticator_class = "generic-oauth"

# OAuth2 application info
# -----------------------
c.GenericOAuthenticator.oauth_callback_url = "http://localhost:9000/hub/oauth_callback"
c.GenericOAuthenticator.client_id = "nomad_public"
c.GenericOAuthenticator.client_secret = ""

# Identity provider info
# ----------------------
# https://nomad-lab.eu/fairdi/keycloak/auth/realms/fairdi_nomad_test/.well-known/openid-configuration
c.GenericOAuthenticator.authorize_url = "https://nomad-lab.eu/fairdi/keycloak/auth/realms/fairdi_nomad_test/protocol/openid-connect/auth"
c.GenericOAuthenticator.token_url = "https://nomad-lab.eu/fairdi/keycloak/auth/realms/fairdi_nomad_test/protocol/openid-connect/token"
c.GenericOAuthenticator.userdata_url = "https://nomad-lab.eu/fairdi/keycloak/auth/realms/fairdi_nomad_test/protocol/openid-connect/userinfo"

# What we request about the user
# ------------------------------
# scope represents requested information about the user, and since we configure
# this against an OIDC based identity provider, we should request "openid" at
# least.
#
# In this example we include "email" and "groups" as well, and then declare that
# we should set the username based on the "email" key in the response, and read
# group membership from the "groups" key in the response.
#
c.GenericOAuthenticator.scope = ["openid", "email"]
c.GenericOAuthenticator.username_claim = "preferred_username"
# c.GenericOAuthenticator.auth_state_groups_key = "oauth_user.groups"

# Authorization
# -------------
c.GenericOAuthenticator.allow_all = True
c.GenericOAuthenticator.admin_users = {"test"}


c.GenericOAuthenticator.login_service = "Keycloak"


#     config:
#       Authenticator:
#         auto_login: true
#         enable_auth_state: true
#         username_key: preferred_username
#         userdata_params:
#           state: state
# jupyterhub:
#   fullnameOverride: "nomad-prod-staging-north"
#   hub:
#     baseUrl: "/prod/v1/staging/north"
#     config:
#       GenericOAuthenticator:
#         oauth_callback_url: https://nomad-lab.eu/prod/v1/staging/north/hub/oauth_callback
#   singleuser:
#     podNameTemplate: "nomad-prod-staging-north-{username}--{servername}"


c.JupyterHub.hub_ip = "0.0.0.0"  # listen on all interfaces
c.JupyterHub.hub_connect_ip = (
    "hub"  # IP as seen on the docker network. Can also be a hostname.
)

c.JupyterHub.allow_named_servers = True


# c.JupyterHub.template_paths = ['/srv/jupyterhub/templates']
c.JupyterHub.logo_file = "/srv/jupyterhub/logo/fairmat_logo.svg"



async def user_redirect_hook(path, request, user, base_url):
    """Changing the the behavior of /user-redirect/ url
    Instead of using default server the first path must be
    the name of the named server
    """
    server_name = path.split("/", 1)[0]

    user_url = url_path_join(user.url, path)

    if request.query:
        user_url = url_concat(user_url, parse_qsl(request.query))

    url = url_concat(
        url_path_join(
            base_url,
            "spawn",
            user.escaped_name,
            server_name,
        ),
        {"next": user_url},
    )
    return url


c.JupyterHub.user_redirect_hook = user_redirect_hook


#     02-custom-spawner.py: |
#       import asyncio
#       from kubespawner import KubeSpawner
#       from traitlets import default
#
#       class CustomSpawner(KubeSpawner):
#
#         def __init__(self, *args, **kwargs):
#           self.log.debug(f"CustomSpawner::__init__")
#           self.log.debug(f"CustomSpawner::__init__ args: {args}")
#           self.log.debug(f"CustomSpawner::__init__ kwargs: {kwargs}")
#           super().__init__(*args, **kwargs)
#
#         async def start(self):
#           """Start the user's pod"""
#
#           self.log.debug("CustomSpawner::start")
#
#           return (await super().start())
#
#         @default('pre_spawn_hook')
#         def _pre_spawn_hook(self):
#
#             self.log.debug(f"CustomSpawner::pre_spawn_hook")
#
#         #     # Overwriting the profile name to match with the server name
#         #     if self.name:
#         #       self.user_options["profile"] = self.name
#         #
#         #     # This returns with an error if the chosen profile doesn't exist
#         #     self.load_user_options()
#
#
#         def _options_form_default(self):
#           """Custom option form callable function to only show profiles
#           for the default server and not for the named servers.
#           """
#
#           self.log.debug(f"CustomSpawner::options_form")
#
#           # Do not show forms for named servers
#           if self.name:
#             return ''
#
#           return super()._options_form_default()
#
#         async def load_user_options(self):
#
#           self.log.debug("CustomSpawner::load_user_options")
#
#           # Overwrite the profile name to match with the server name
#           if self.name:
#             self.user_options["profile"] = self.name
#
#           await super().load_user_options()
#
#       c.JupyterHub.spawner_class = CustomSpawner
#
#
#
