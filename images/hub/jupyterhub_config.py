import os

import requests
import functools

from enum import Enum
from traitlets import default
from tornado import web
from tornado.httputil import url_concat
from urllib.parse import parse_qsl
from jinja2 import Environment, FileSystemLoader
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
        'always', 
        description='The image pull policy used in k8s deployments.'
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
            'Whether the path of the file or directory the tool was launched '
            'from is forwarded to the tool so it can open or deep-link to that '
            'item. When enabled, the launcher passes the item path to the tool: '
            'in the old hub it is appended to the tool URL via `path_prefix` '
            '(e.g. `lab/tree/<path>` for JupyterLab); in the new hub it is sent '
            'as `upload_id`/`path` query parameters for a handler inside the '
            'tool image to consume. Only meaningful for tools that can open a '
            'file from a URL (e.g. JupyterLab); set to False for tools that '
            'cannot, such as desktop (noVNC) apps like VESTA or FIJI. This does '
            'NOT control whether the tool is offered for a file in the NOMAD '
            'UI. That is determined solely by `file_extensions`.'
        ),
    )
    file_extensions: list[str] = Field(
        [],
        description='The file extensions of files that this tool should be launchable for.',
    )
    mount_path: str = Field(
        "/home/jovyan",
        description=(
            'The path in the container where uploads and work directories will be mounted, '
            'e.g. /home/jovyan for Jupyter containers.'
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

Profile.model_rebuild()


class Config(BaseSettings):
    nomad_api_url: str = Field(
        "http://app:8000/nomad-oasis/api/v1", description="URL of the Nomad API")
    hub_port: int = Field(9000, description="Port for the JupyterHub")
    base_url: str = Field("/nomad-oasis/north/",
                          description="Base URL for the JupyterHub")
    hub_ip: str = Field("0.0.0.0", description="IP address for the JupyterHub")
    hub_connect_ip: str = Field(
        "north", description="IP address for the JupyterHub to connect to")
    admin_users: list[str] = Field([], description="List of admin users")
    oauth_callback_url: str = Field(
        "http://localhost/nomad-oasis/north/hub/oauth_callback", description="OAuth callback URL")
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


config = Config()


class NORTHSpawner(DockerSpawner):

    nomad_api_url = config.nomad_api_url

    @functools.cached_property
    def profile_list(self):
        api_url = f"{self.nomad_api_url}/north/tools/"
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

    def options_from_query(self, query_options):
        options = super().options_from_query(query_options)

        upload_id = query_options.get('upload_id', [None])[0]
        path = query_options.get('path', [None])[0]

        if upload_id:
            options["upload_id"] = upload_id
        if path:
            options["path"] = path

        self.log.info(f"[NORTH options_from_query] upload_id: {upload_id}, path: {path}")

        return options

    def run_options_from_form(self, form_options):
        self.log.info(f"[NORTH run_options_from_form] form_options: {form_options}")

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

        spawner.log.info(
            f"[NORTH auth_state_hook] access_token: {spawner.user_options['access_token']}"
        )


    @default('pre_spawn_hook')
    def _pre_spawn_hook(spawner):

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

            mounts = []
            upload_ids = {}
            for mount in response.json():
                mounts.append({
                    'type': 'bind',
                    'source': mount['source'],
                    'target': mount['target'],
                    'read_only': mount['mode'] != 'rw'
                })
                if 'upload_id' in mount and mount['upload_id'] is not None:
                    mount_path = profile.mount_path or ''
                    upload_ids[mount['upload_id']] = mount['target'][len(mount_path):]

            spawner.mounts = mounts
            spawner.user_options["upload_ids"] = upload_ids


            # Read URL parameters stored in user_options
            upload_id = spawner.user_options.get('upload_id')
            rel_path = spawner.user_options.get('path')

            if profile.with_path and upload_id and upload_id in upload_ids:
                
                if rel_path:
                    full_path = os.path.join(upload_ids[upload_id], rel_path.lstrip('/'))
                else:
                    full_path = upload_ids[upload_id]

                # Determine route prefix (e.g. use path_prefix eg: "lab/tree" )
                prefix = profile.path_prefix.strip('/')
                spawner.default_url = f"/{prefix}/{full_path.lstrip('/')}"

            else:
                # Standard landing page without explicit path deep-linking
                spawner.default_url = profile.default_url

            spawner.log.info(
                f"[NORTH pre_spawn_hook] Profile: '{profile.slug}' | "
                f"Resolved default_url: '{spawner.default_url}'"
            )


async def user_redirect_hook(path, request, user, base_url):
    """Changing the the behavior of /user-redirect/ url
    Instead of using default server the first path must be
    the name of the named server
    """
    server_name = path.split("/", 1)[0]

    # Get or instantiate the spawner for this server
    spawner = user.spawners.get(server_name) or user.get_spawner(server_name)
    profile = spawner._get_profile(spawner.name)
    spawner.log.info(
            f"[NORTH user_redirect_hook] path={path} server_name={server_name} profil={profile}"
        )
    spawner.log.info(
            f"[NORTH user_redirect_hook] path={path} server_name={server_name} spawner.ready={spawner.ready} spawner.active={spawner.active} spawner.pending={spawner.pending} spawner.state={spawner.state} spawner.user_options={spawner.user_options}"
        )
    
    # If the server is stopped, forward to spawn flow
    if not spawner.ready:
        spawner.log.info(
            f"[NORTH user_redirect_hook] server_name={server_name} NOT READY, redirecting to spawn flow"
        )
        # spawn_url = f"/hub/spawn/{user.name}/{server_name}" if server_name else f"/hub/spawn/{user.name}"

        # if request.query:
        #     spawn_url += f"?{request.query}"
 
        # return spawn_url


    # (Handle active server redirect here if already running...)

    #         url_path_join(
    #         base_url,
    #         "spawn",
    #         user.escaped_name,
    #         server_name

    # return f"/user/{user.name}/{server_name}/lab"


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

    spawner.log.info(
        f"[NORTH user_redirect_hook] path={path} server_name={server_name} "
        f"user={user.name} next_url={url} query={request.query} base_url={base_url} user_url={user.url}"
    )
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
c.DockerSpawner.debug = True


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
