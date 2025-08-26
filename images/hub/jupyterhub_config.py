import os
import yaml
import logging

from functools import lru_cache

from tornado import web
from tornado.httputil import url_concat
from urllib.parse import parse_qsl
from jinja2 import Environment, FileSystemLoader

from dockerspawner import DockerSpawner
from jupyterhub.utils import url_path_join

c = get_config()  # type: ignore # noqa: F821


@lru_cache
def _load_value_file():
    """Load the config values from file(s)"""

    path = "profile_list.yaml"

    if not os.path.exists(path):
        print(f"No config at {path}")
        return {}

    print(f"Loading {path}")
    with open(path) as f:
        try:
            cfg = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            logging.warning(exc)

    return cfg

    NORTHSpawner.profile_list = config.get("profile_list", [])


def get_value(key, default=None):
    """
    Find a config item of a given name & return it

    Parses everything as YAML, so lists and dicts are available too

    get_config("a.b.c") returns config['a']['b']['c']
    """
    value = _load_value_file()
    # resolve path in yaml
    for level in key.split("."):
        if not isinstance(value, dict):
            # a parent is a scalar or null,
            # can't resolve full path
            return default
        if level not in value:
            return default
        else:
            value = value[level]
    return value


class NORTHSpawner(DockerSpawner):

    profile_list = None

    def _options_form_default(self):
        self.log.info(
            "!!!!!!!!!!!!!!!!!! _options_form_default !!!!!!!!!!!!!!!!!")

        if not self.profile_list:
            return ''

        # Do not show forms for named servers
        if self.name:
            return ''

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
        self.log.info("!!!!!!!!!!!!!!!!!! options_from_form !!!!!!!!!!!!!!!!!")

        options = {}
        # self.log.info(formdata)

        profile_slug = formdata.get("profile", [None])[0]

        if profile_slug:
            options["profile"] = profile_slug

        # self.log.info(f"Generating options: {options}")

        return options

#     def load_user_options(self, options):
#         self.log.info(f"load options: {options}")
#
#         profile = self._get_profile(options["profile"])
#         self.log.info(f"load profile: {profile}")
#
#         image = profile["dockerspawner_override"]["image"]
#         if image:
#             self.log.info(f"Loading image {image}")
#             self.image = image

    def run_options_from_form(self, form_options):
        self.log.info(
            "!!!!!!!!!!!!!!!!!!!!!!l run_options_from_form !!!!!!!!!!!!!!!!!!!!!!")
        print(form_options)

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

    @staticmethod
    def auth_state_hook(spawner, auth_state):
        spawner.log.info(
            f"!!!!!!!!!!!!!!!!!!!!!!l auth_state_hook ({spawner.name}) !!!!!!!!!!!!!!!!!!!!!!")
        spawner.log.info(auth_state)

        if not spawner.name:
            return


c.Spawner.auth_state_hook = "NORTHSpawner.auth_state_hook"

# Loding the profile list from file
with open("profile_list.yaml") as stream:
    try:
        config = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        logging.warning(exc)

    NORTHSpawner.profile_list = config.get("profile_list", [])


def my_hook(spawner):
    spawner.log.info("!!!!!!!!!!!!!!!!! pre_spawn_hook !!!!!!!!!!!!!!!!!")
    if spawner.name:
        if spawner.name not in [p['slug'] for p in spawner.profile_list]:
            # spawner.remove_object()
            raise web.HTTPError(403, "This profile is not allowed")

        spawner.image = spawner._get_profile(
            spawner.name)['dockerspawner_override']['image']


c.Spawner.pre_spawn_hook = my_hook


# We rely on environment variables to configure JupyterHub so that we
# avoid having to rebuild the JupyterHub container every time we change a
# configuration parameter.

# Spawn single-user servers as Docker containers
# c.JupyterHub.spawner_class = "dockerspawner.DockerSpawner"
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


# Authentication
c.JupyterHub.authenticator_class = "generic-oauth"

# OAuth2 application info
# -----------------------
c.GenericOAuthenticator.oauth_callback_url = "http://localhost:9000/fairdi/nomad/latest/north/hub/oauth_callback"
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
c.GenericOAuthenticator.auto_login = True


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

c.JupyterHub.base_url = "/fairdi/nomad/latest/north/"
c.JupyterHub.hub_ip = "0.0.0.0"  # listen on all interfaces
c.JupyterHub.hub_connect_ip = (
    "hub"  # IP as seen on the docker network. Can also be a hostname.
)

c.JupyterHub.allow_named_servers = True

# c.JupyterHub.template_paths = ['/srv/jupyterhub/templates']
c.JupyterHub.logo_file = "/srv/jupyterhub/logo/nomad_logo.svg"


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
