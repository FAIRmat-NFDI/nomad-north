import os
import requests
import logging
import functools

from traitlets import default
from tornado import web
from tornado.httputil import url_concat
from urllib.parse import parse_qsl
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel, Field

from dockerspawner import DockerSpawner
from jupyterhub.utils import url_path_join

c = get_config()  # type: ignore # noqa: F821

logger = logging.getLogger(__name__)



class Profile(BaseModel):
    display_name: str = Field(..., description="Name of the profile")
    default: bool = Field(False, description="Is this the default profile?")
    description: str = Field(..., description="Description of the profile")
    slug: str = Field(..., description="Slug for the profile")
    image: str = Field(..., description="Docker image for the profile")
    default_url: str = Field(
        "/lab", description="Default URL to open when the profile is started")



class NORTHSpawner(DockerSpawner):

    nomad_api_url = os.environ.get("NOMAD_API_URL", "http://app:8000/nomad-oasis/api/v1")

    @functools.cached_property
    def profile_list(self):
        api_url = f"{self.nomad_api_url}/north/tools/"
        response = requests.get(api_url)
        profile_list = []
        for tool in response.json():
            profile_list.append(
                Profile(
                    display_name=tool['name'],
                    description=tool['short_description'],
                    slug=tool['name'],
                    image=tool['image'],
                    default_url=tool['default_url']
                )
            )
        return profile_list


    def _options_form_default(self):
        """Custom option form callable function to only show profiles
        for the default server and not for the named servers.
        """
        self.log.info("!!!!!! _options_form_default !!!!!!")

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
        """Called by jupyterhub when processing a request to spawn a server, where
        the user either have submitted a POST request via a form or submitted a
        GET request with query parameters.

        Args:
            formdata: user selection returned by the form

        Returns:
            user_options (dict): the selected profile in the user_options form,
                e.g. ``{"profile": "cpus-8"}``
        """
        self.log.info("!!!!!! options_from_form !!!!!!")

        options = {}
        # self.log.info(formdata)

        profile_slug = formdata.get("profile", [None])[0]

        if profile_slug:
            options["profile"] = profile_slug


        return options


    def run_options_from_form(self, form_options):
        self.log.info(
            "!!!!!!l run_options_from_form !!!!!!")
        print(form_options)

    def _get_profile(self, slug: str):
        """
        Returns the profile from profile_list matching given slug, or the
        (first) default profile if slug is falsy.

        profile_list is required to have a default profile.

        Raises an error if no profile exists for the given slug.
        """

        for profile in self.profile_list:
            if profile.slug == slug:
                # return matching profile
                return profile

        raise ValueError(
            "No such profile: %s. Options include: %s"
            % (slug, ', '.join(p.slug for p in self.profile_list))
        )

    @staticmethod
    def auth_state_hook(spawner, auth_state):
        spawner.log.info(f"!!!!!! auth_state_hook ({spawner.name}) !!!!!!")

        if not spawner.name:
            return

        spawner.user_options["access_token"] = auth_state["access_token"]



    @default('pre_spawn_hook')
    def _pre_spawn_hook(spawner):
        # spawner.log.info("!!!!!! pre_spawn_hook !!!!!!")

        if spawner.name:
            if spawner.name not in [p.slug for p in spawner.profile_list]:
                # spawner.remove_object()
                raise web.HTTPError(403, "This profile is not allowed")

            spawner.image = spawner._get_profile(spawner.name).image


        api_url = f"{spawner.nomad_api_url}/north/mounts/{spawner.name}"
        api_headers = {"Authorization": f"Bearer {spawner.user_options.get('access_token')}"}

        response = requests.get(api_url, headers=api_headers)
        spawner.log.info(f"api_url: {api_url}")
        spawner.log.info(response.status_code)
        spawner.log.info(response.json())

        mounts = []
        for mount in response.json():
            mounts.append({
                'type': 'bind',
                'source': mount['source'],
                'target': mount['target'],
                'read_only': mount['mode'] != 'rw'
            })
        spawner.mounts = mounts



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


# Hub configuration
# -----------------
# We rely on environment variables to configure JupyterHub so that we
# avoid having to rebuild the JupyterHub container every time we change a
# configuration parameter.


# User containers will access hub by container name on the Docker network
# c.JupyterHub.hub_ip = os.environ.get("HUB_IP", "localhost")
# c.JupyterHub.hub_connect_ip = "north"
c.JupyterHub.port = os.environ.get("HUB_PORT", 9000)
c.JupyterHub.base_url = os.environ.get("BASE_URL", "/nomad-oasis/north/")
# By default listen on all interfaces to be accessible from outside the container
c.JupyterHub.hub_ip = os.environ.get("HUB_IP", "0.0.0.0")
# IP as seen on the docker network. Can also be a hostname.
c.JupyterHub.hub_connect_ip = os.environ.get("HUB_CONNECT_IP", "north")


# Persist hub data on volume mounted inside container
c.JupyterHub.cookie_secret_file = "/data/jupyterhub_cookie_secret"
c.JupyterHub.db_url = "sqlite:////data/jupyterhub.sqlite"


c.JupyterHub.allow_named_servers = True
c.JupyterHub.shutdown_on_logout = True

# c.JupyterHub.template_paths = ['/srv/jupyterhub/templates']
c.JupyterHub.logo_file = "/srv/jupyterhub/logo/nomad_logo.svg"


# Authentication
# -------------

c.JupyterHub.authenticator_class = "generic-oauth"
c.JupyterHub.user_redirect_hook = user_redirect_hook

c.Authenticator.allow_all = True
c.Authenticator.auto_login = True
c.Authenticator.refresh_pre_spawn = True
c.Authenticator.enable_auth_state = True
c.Authenticator.admin_users = os.environ.get("ADMIN_USERS", "").split(",")

# Users do not have permission to read their own auth state by default, but auth_state is where the access_token is stored.
# https://oauthenticator.readthedocs.io/en/latest/how-to/refresh.html#refreshing-tokens-from-user-sessions
c.JupyterHub.load_roles = [
    {
        "name": "user",
        "scopes": [
            "self",
            "admin:auth_state!user",
        ],
    },
    {
        "name": "server",
        "scopes": [
            "users:activity!user",
            "access:servers!server",
            "admin:auth_state!user",
        ],
    },
]


# What we request about the user
# ------------------------------
# scope represents requested information about the user, and since we configure
# this against an OIDC based identity provider, we should request "openid" at
# least.

c.GenericOAuthenticator.login_service = "keycloak"
c.GenericOAuthenticator.scope = ["openid", "profile"]
c.GenericOAuthenticator.username_claim = "preferred_username"

# OAuth2 application info
# -----------------------
# Note: callback_url should be an url accessible from "outside"
c.GenericOAuthenticator.oauth_callback_url = os.environ.get(
    "HUB_OAUTH_CALLBACK_URL", "http://localhost:9000/nomad-oasis/north/hub/oauth_callback")
c.GenericOAuthenticator.client_id = os.environ.get("OAUTH_CLIENT_ID", "public")
c.GenericOAuthenticator.client_secret = os.environ.get(
    "OAUTH_CLIENT_SECRET", "")

# Identity provider info
# ----------------------
# https://nomad-lab.eu/fairdi/keycloak/auth/realms/fairdi_nomad_test/.well-known/openid-configuration
c.GenericOAuthenticator.userdata_params = {"state": "state"}
c.GenericOAuthenticator.authorize_url = os.environ.get(
    "HUB_AUTHORIZE_URL", "https://nomad-lab.eu/fairdi/keycloak/auth/realms/fairdi_nomad_test/protocol/openid-connect/auth")
c.GenericOAuthenticator.token_url = os.environ.get(
    "HUB_TOKEN_URL", "https://nomad-lab.eu/fairdi/keycloak/auth/realms/fairdi_nomad_test/protocol/openid-connect/token")
c.GenericOAuthenticator.userdata_url = os.environ.get(
    "HUB_USERDATA_URL", "https://nomad-lab.eu/fairdi/keycloak/auth/realms/fairdi_nomad_test/protocol/openid-connect/userinfo")



# Spawn single-user servers as Docker containers
c.JupyterHub.spawner_class = NORTHSpawner


# For debugging arguments passed to spawned containers
c.DockerSpawner.debug = False


# Remove containers once they are stopped
c.DockerSpawner.remove = True

# Prefix for container names. See name_template for full container name for a particular
# user's server. (Default: 'jupyter')
c.DockerSpawner.prefix = os.environ.get(
    "DOCKER_PREFIX", "nomad-oasis-north")

# Connect containers to this Docker network
c.DockerSpawner.use_internal_ip = True
c.DockerSpawner.network_name = os.environ.get(
    "DOCKER_NETWORK", "nomad_oasis_network")


# Fixing: Unexpected error: "Gateway Time-out (504)".
# Please try again and let us know, if this error keeps happening.
c.DockerSpawner.http_timeout = 5 * 60  # in seconds
c.DockerSpawner.start_timeout = 10 * 60  # in seconds



# configure nomad service
c.JupyterHub.services.append(
    {
        'name': 'nomad-service',
        'admin': True,
        'api_token': os.environ.get(
    "SERVICE_API_TOKEN", "secret-token"),
    }
)