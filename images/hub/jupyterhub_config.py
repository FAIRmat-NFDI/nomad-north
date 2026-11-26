import os

import requests
import functools

from enum import Enum
from traitlets import default
from tornado import web
from tornado.httputil import url_concat
from tornado.log import app_log
from urllib.parse import parse_qsl, quote
from jinja2 import Environment, FileSystemLoader
from jinjaMarkdown.markdownExtension import markdownExtension
from typing import ClassVar

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

from dockerspawner import DockerSpawner
from jupyterhub.utils import url_path_join

import logging

logger = logging.getLogger(__name__)

c = get_config()  # type: ignore # noqa: F821

if 'JUPYTERHUB_CRYPT_KEY' not in os.environ:
    os.environ["JUPYTERHUB_CRYPT_KEY"] = os.urandom(32).hex()


class NORTHToolMaintainer(BaseModel):
    name: str
    email: str


class ReadMode(str, Enum):
    ro = 'ro'
    rw = 'rw'


class NORTHExternalMount(BaseModel):
    host_path: str
    bind: str
    mode: ReadMode = ReadMode.ro


class Profile(BaseModel):
    slug: str = Field(..., description="Slug for the profile")
    default: bool = Field(False, description="Is this the default profile?")
    display_name: str = Field(..., description="Name of the profile")
    short_description: str = Field(
        ...,
        description='A short description of the tool, e.g. shown in the NOMAD GUI.',
    )
    description: str | None= Field(
        None, 
        description='A description of the tool, e.g. shown in the NOMAD GUI.'
    )
    image: str = Field(
        ..., 
        description="The docker image (incl. tags) to use for the tool."
    )
    default_url: str | None = Field(
        "/lab",
        description=(
            'An optional path prefix that is added to the container URL to '
            'reach the tool, e.g. "/lab" for jupyterlab.'
        ),
    )
    image_pull_policy: str = Field(
        'Always',
        description=(
            'The image pull policy, as set by NOMAD: "Always", "IfNotPresent" '
            'or "Never". DockerSpawner treats these values case-insensitively. '
            '"Always" pulls the image on every spawn and fails for images that '
            'only exist locally.'
        ),
    )
    cmd: str | None = Field(
        None, 
        description='The container cmd that is passed to the spawner.'
    )
    privileged: bool = Field(
        False, 
        description='Whether the tool needs to run in privileged mode.'
    )
    seccomp_unconfined: bool = Field(
        False, 
        description='Whether the tool runs with seccomp=unconfined.'
    )
    use_gpu: bool = Field(
        False,
        description='Whether the tool is provided access to available GPU resources.',
    )
    path_prefix: str = Field(
        'lab/tree',
        description=(
            'An optional path prefix that is added to the container URL to '
            'reach the files, e.g. "lab/tree" for jupyterlab.'
        ),
    )
    with_path: bool = Field(
        False,
        description=(
            'Whether the tool is opened at the project, folder or file it was '
            'launched from. When enabled with the new hub, the NOMAD UI opens '
            '`<north_base>/user-redirect/<tool>/<path_prefix>/projects/<project_id>/<path>`, '
            "which the hub forwards to the user's server for this tool, starting "
            'it first if needed; projects are mounted at '
            '`<mount_path>/projects/<project_id>`. With the old hub, the UI opens '
            '`<path_prefix>/<upload_mount_dir>/<path>` instead. When disabled, '
            'the tool is opened at its `default_url`. Only meaningful for tools '
            'that can open a file from a URL (e.g. JupyterLab, where `path_prefix` '
            'is "lab/tree"); set to False for tools that cannot, such as desktop '
            '(noVNC) apps like VESTA or FIJI. This does NOT control whether the '
            'tool is offered for a file in the NOMAD UI. That is determined '
            'solely by `file_extensions`.'
        ),
    )
    file_extensions: list[str] = Field(
        [],
        description='The file extensions of files that this tool should be launchable for.',
    )
    mount_path: str = Field(
        "/home/jovyan",
        description=(
            'The path in the container where the projects and the work directory '
            'are mounted, e.g. /home/jovyan for Jupyter containers.'
        ),
    )
    icon: str | None = Field(
        None,
        description='A URL to an icon that is used to represent the tool in the NOMAD UI.',
    )
    maintainer: list[NORTHToolMaintainer] = Field(
        [], description='The maintainers of the tool.'
    )
    external_mounts: list[NORTHExternalMount] = Field(
        [], description='Additional mounts to be added to tool containers.'
    )

# JupyterHub loads this file with traitlets' PyFileConfigLoader, which compiles it
# with `from __future__ import annotations` inherited from traitlets itself. All
# annotations above are therefore strings, and pydantic cannot resolve names like
# `ReadMode` because the classes' `__module__` is `builtins`. `model_rebuild()`
# resolves them from the namespace of the calling frame (this file). Every model
# that is instantiated directly must be rebuilt, not only the outermost one.
NORTHToolMaintainer.model_rebuild()
NORTHExternalMount.model_rebuild()
Profile.model_rebuild()


class Config(BaseSettings):
    nomad_api_url: str = Field(
        "http://app:8000/backend/api/v1", description="URL of the Nomad API")
    hub_port: int = Field(9000, description="Port for the JupyterHub")
    base_url: str = Field("/backend/north/",
                          description="Base URL for the JupyterHub")
    hub_ip: str = Field("0.0.0.0", description="IP address for the JupyterHub")
    hub_connect_ip: str = Field(
        "north", description="IP address for the JupyterHub to connect to")
    admin_users: list[str] = Field([], description="List of admin users")
    oauth_callback_url: str = Field(
        "http://localhost/backend/north/hub/oauth_callback", description="OAuth callback URL")
    keycloak_url: str = Field(
        "https://nomad-lab.eu/fairdi/keycloak/auth", description="Base URL for Keycloak")
    keycloak_realm: str = Field(
        "fairdi_nomad_test", description="Keycloak realm")
    oauth_client_id: str = Field("public", description="OAuth client ID")
    oauth_client_secret: str = Field("", description="OAuth client secret")
    docker_prefix: str = Field(
        "nomad-oasis-north", description="Prefix for Docker container names")
    docker_network: str = Field(
        "nomad_oasis_network", description="Docker network name")
    service_api_token: str = Field(
        "", description="API token for the Nomad service")
    nomad_app_token_expires_in: int = Field(
        24 * 3600, description="Lifetime in seconds of the NOMAD app token passed to tools")

config = Config()

def get_profile_list(nomad_api_url):
    api_url = f"{nomad_api_url}/north/tools/"
    response = requests.get(api_url)
    profile_list = []
    for tool in response.json():
        profile_list.append(
            Profile(
                slug=tool['name'],
                display_name=tool['name'],
                short_description=tool['short_description'],
                description=tool['description'],
                image=tool['image'],
                default_url=tool['default_url'],
                image_pull_policy=tool['image_pull_policy'],
                cmd=tool['cmd'],
                privileged=tool['privileged'],
                seccomp_unconfined=tool['seccomp_unconfined'],
                use_gpu=tool['use_gpu'],
                path_prefix=tool['path_prefix'],
                with_path=tool['with_path'],
                file_extensions=tool['file_extensions'],
                mount_path=tool['mount_path'],
                icon=tool['icon'],
                maintainer=[NORTHToolMaintainer(**m) for m in tool['maintainer']],
                external_mounts=[NORTHExternalMount(**m) for m in tool['external_mounts']]  
            )
        )
    return profile_list


class NORTHSpawner(DockerSpawner):

    nomad_api_url: ClassVar[str] = config.nomad_api_url
    profile_list: ClassVar[list[Profile]] = get_profile_list(config.nomad_api_url)

    def _options_form_default(self):
        """Custom option form callable function to only show profiles
        for the default server and not for the named servers.
        """

        if not self.profile_list:
            return ''

        # Do not show forms for named servers
        if self.name:
            return ''

        loader = FileSystemLoader('/srv/jupyterhub/templates')
        env = Environment(loader=loader)

        env.add_extension(markdownExtension)

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

        options = {}

        profile_slug = formdata.get("profile", [None])[0]

        if profile_slug:
            options["profile"] = profile_slug

        self.log.info(f"[NORTH options_from_form] profile_slug: {profile_slug}")

        return options


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

        if not spawner.name:
            return

        spawner.user_options["access_token"] = auth_state["access_token"]


    @default('pre_spawn_hook')
    def _pre_spawn_hook(spawner):

        spawner.log.info(
            f"[NORTH pre_spawn_hook] spawner.name: {spawner.name}"
        )

        if spawner.name:
            if spawner.name not in [p.slug for p in spawner.profile_list]:
                # spawner.remove_object()
                raise web.HTTPError(403, "This profile is not allowed")

            profile = spawner._get_profile(spawner.name)
            spawner.image = profile.image
            spawner.default_url = profile.default_url

            if profile.image_pull_policy:
                spawner.pull_policy = profile.image_pull_policy

            if profile.cmd:
                spawner.cmd = profile.cmd

            if profile.privileged:
                spawner.extra_host_config['privileged'] = True

            if profile.seccomp_unconfined:
                spawner.extra_host_config['security_opt'] = ['seccomp=unconfined']

            if profile.use_gpu:
                import docker

                # Check if any GPUs are available
                nvidia_version_path = '/proc/driver/nvidia/version'
                if os.path.exists(nvidia_version_path):
                    with open(nvidia_version_path) as file:
                        version_info = file.read()
                    spawner.log.info(
                        f'Enabling nvidia driver with driver info:\n{version_info}.'
                    )
                    spawner.extra_host_config['device_requests'] = [
                        docker.types.DeviceRequest(  # type: ignore
                            count=-1,
                            capabilities=[['gpu']],
                        ),
                    ]
                else:
                    spawner.log.warning(
                        'GPU requested but no nvidia driver found on host. Disabling GPU usage for this container.'
                    )
                    profile.use_gpu = False

            api_url = f"{spawner.nomad_api_url}/north/mounts/{spawner.name}"
            api_headers = {
                "Authorization": f"Bearer {spawner.user_options.get('access_token')}"}

            response = requests.get(api_url, headers=api_headers)

            # NOMAD decides where things are mounted, e.g. projects at
            # <mount_path>/projects/<project_id>, so use its targets as they are.
            mounts = []
            upload_ids = {}
            for mount in response.json():
                mounts.append({
                    'type': 'bind',
                    'source': mount['source'],
                    'target': mount['target'],
                    'read_only': mount['mode'] != 'rw'
                })
                if mount.get('upload_id') is not None:
                    upload_ids[mount['upload_id']] = mount['target']

            spawner.mounts = mounts
            spawner.user_options["upload_ids"] = upload_ids

            # Pass a NOMAD app token to the tool, e.g. for API calls from notebooks.
            api_url = f"{spawner.nomad_api_url}/auth/app_token"
            response = requests.get(
                api_url,
                params={"expires_in": config.nomad_app_token_expires_in},
                headers=api_headers,
            )
            response.raise_for_status()

            spawner.environment["NOMAD_CLIENT_USER"] = spawner.user.name
            spawner.environment["NOMAD_CLIENT_ACCESS_TOKEN"] = response.json()["app_token"]
            # nomad.client appends /v1 to config.client.url itself.
            spawner.environment["NOMAD_CLIENT_URL"] = spawner.nomad_api_url.removesuffix("/v1")

            spawner.log.info(
                f"[NORTH pre_spawn_hook] NOMAD_CLIENT_* env set for user: {spawner.user.name}"
            )

            spawner.log.info(
                f"[NORTH pre_spawn_hook] Profile: '{profile.slug}' | "
                f"Resolved default_url: '{spawner.default_url}'"
            )

def apply_user_options(spawner, user_options):
    """Called by jupyterhub when processing a request to spawn a server, where
    the user either have submitted a POST request via a form or submitted a
    GET request with query parameters.

    Args:
        spawner: The spawner instance
        user_options: User selection returned by the form

    Returns:
        None
    """
    if not spawner.name:
        profile_slug = user_options.get("profile", None)

        profile = spawner._get_profile(profile_slug)
        spawner.image = profile.image
        spawner.default_url = profile.default_url

        if profile.image_pull_policy:
            spawner.pull_policy = profile.image_pull_policy

        if profile.cmd:
            spawner.cmd = profile.cmd

        if profile.privileged:
            spawner.extra_host_config['privileged'] = True

        if profile.seccomp_unconfined:
            spawner.extra_host_config['security_opt'] = ['seccomp=unconfined']

        if profile.use_gpu:
            import docker

            # Check if any GPUs are available
            nvidia_version_path = '/proc/driver/nvidia/version'
            if os.path.exists(nvidia_version_path):
                with open(nvidia_version_path) as file:
                    version_info = file.read()
                spawner.log.info(
                    f'Enabling nvidia driver with driver info:\n{version_info}.'
                )
                spawner.extra_host_config['device_requests'] = [
                    docker.types.DeviceRequest(  # type: ignore
                        count=-1,
                        capabilities=[['gpu']],
                    ),
                ]
            else:
                spawner.log.warning(
                    'GPU requested but no nvidia driver found on host. Disabling GPU usage for this container.'
                )
                profile.use_gpu = False
        
        spawner.log.info(f"[NORTH apply_user_options] profile_slug: {profile_slug}")



async def user_redirect_hook(path, request, user, base_url):
    """Redirect `/hub/user-redirect/<server_name>/<path>` to `<path>` on the
    user's named server `<server_name>`, starting the server first if needed.

    NOMAD links to a project, folder or file in a tool with a fully formed path,
    e.g. `/hub/user-redirect/jupyter/lab/tree/projects/<project_id>/<path>`.
    The hub only forwards the path (and query) to the tool; how a project, folder
    or file maps to a URL is defined by the tool (see `Profile.with_path`).
    """
    server_name = path.split("/", 1)[0]

    # Tornado has already decoded the path; encode it again for the redirect.
    user_url = url_path_join(user.url, quote(path))
    if request.query:
        user_url = url_concat(
            user_url, parse_qsl(request.query, keep_blank_values=True)
        )

    # The spawn handler starts the server if it is not running, waits until it
    # is ready and then redirects to `next`.
    url = url_concat(
        url_path_join(base_url, "hub", "spawn", user.escaped_name, server_name),
        {"next": user_url},
    )
    app_log.info(f"[NORTH user_redirect_hook] path={path} url={url}")
    return url


# Hub configuration
# -----------------
# We rely on environment variables to configure JupyterHub so that we
# avoid having to rebuild the JupyterHub container every time we change a
# configuration parameter.


# User containers will access hub by container name on the Docker network
# c.JupyterHub.hub_ip = "localhost"
# c.JupyterHub.hub_connect_ip = "north"
c.JupyterHub.port = config.hub_port
c.JupyterHub.base_url = config.base_url
# By default listen on all interfaces to be accessible from outside the container
c.JupyterHub.hub_ip = config.hub_ip
# IP as seen on the docker network. Can also be a hostname.
c.JupyterHub.hub_connect_ip = config.hub_connect_ip


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
c.Spawner.apply_user_options = apply_user_options


c.Authenticator.allow_all = True
c.Authenticator.auto_login = True
c.Authenticator.refresh_pre_spawn = True
c.Authenticator.enable_auth_state = True
c.Authenticator.admin_users = config.admin_users

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
c.GenericOAuthenticator.oauth_callback_url = config.oauth_callback_url
c.GenericOAuthenticator.client_id = config.oauth_client_id
c.GenericOAuthenticator.client_secret = config.oauth_client_secret

# Identity provider info
# ----------------------
c.GenericOAuthenticator.userdata_params = {"state": "state"}
c.GenericOAuthenticator.authorize_url = url_path_join(
    config.keycloak_url, f"/realms/{config.keycloak_realm}/protocol/openid-connect/auth")
c.GenericOAuthenticator.token_url = url_path_join(
    config.keycloak_url, f"/realms/{config.keycloak_realm}/protocol/openid-connect/token")
c.GenericOAuthenticator.userdata_url = url_path_join(
    config.keycloak_url, f"/realms/{config.keycloak_realm}/protocol/openid-connect/userinfo")


# Spawn single-user servers as Docker containers
c.JupyterHub.spawner_class = NORTHSpawner


# For debugging arguments passed to spawned containers
c.DockerSpawner.debug = False


# Remove containers once they are stopped
c.DockerSpawner.remove = True

# Prefix for container names. See name_template for full container name for a particular
# user's server. (Default: 'jupyter')
c.DockerSpawner.prefix = config.docker_prefix

# Connect containers to this Docker network
c.DockerSpawner.use_internal_ip = True
c.DockerSpawner.network_name = config.docker_network


# Fixing: Unexpected error: "Gateway Time-out (504)".
# Please try again and let us know, if this error keeps happening.
c.DockerSpawner.http_timeout = 5 * 60  # in seconds
c.DockerSpawner.start_timeout = 10 * 60  # in seconds


# configure nomad service
c.JupyterHub.services.append(
    {
        'name': 'nomad-service',
        'admin': True,
        'api_token': config.service_api_token,
    }
)
