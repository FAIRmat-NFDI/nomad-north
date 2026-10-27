import asyncio
import functools
import os

import requests

from traitlets import default
from tornado import web
from tornado.httputil import url_concat
from urllib.parse import parse_qsl
from jinja2 import Environment, FileSystemLoader
from jinjaMarkdown.markdownExtension import markdownExtension
from pydantic import BaseModel, Field

from kubespawner import KubeSpawner
from jupyterhub.utils import url_path_join

# Not /srv/jupyterhub, which z2jh mounts the hub database volume on.
north_dir = "/usr/local/share/north"


class Profile(BaseModel):
    display_name: str = Field(..., description="Name of the profile")
    default: bool = Field(False, description="Is this the default profile?")
    description: str = Field(..., description="Description of the profile")
    slug: str = Field(..., description="Slug for the profile")
    image: str = Field(..., description="Docker image for the profile")
    default_url: str = Field(
        "/lab", description="Default URL to open when the profile is started"
    )


class NORTHKubeSpawner(KubeSpawner):
    nomad_api_url = os.environ.get(
        "NOMAD_API_URL", "http://nomad-oasis-app:8000/backend/api/v1"
    )

    # Seconds to wait for the NOMAD API before failing a request.
    nomad_api_timeout = float(os.environ.get("NOMAD_API_TIMEOUT", 10))

    nomad_app_token_expires_in = int(
        os.environ.get("NOMAD_ACCESS_TOKEN_EXPIRES_IN", 24 * 3600)
    )

    # Not `profile_list`, which is KubeSpawner's own profile mechanism.
    @functools.cached_property
    def north_profiles(self):
        api_url = f"{self.nomad_api_url}/north/tools/"
        response = requests.get(api_url, timeout=self.nomad_api_timeout)
        response.raise_for_status()
        profile_list = []
        for tool in response.json():
            profile_list.append(
                Profile(
                    display_name=tool["name"],
                    description=tool["short_description"],
                    slug=tool["name"],
                    image=tool["image"],
                    default_url=tool["default_url"],
                )
            )
        return profile_list

    @default("options_form")
    def _options_form_default(self):
        """Custom option form callable function to only show profiles
        for the default server and not for the named servers.
        """

        if not self.north_profiles:
            return ""

        # Do not show forms for named servers
        if self.name:
            return ""

        loader = FileSystemLoader(f"{north_dir}/templates")
        env = Environment(loader=loader)

        # Provides the `markdown` filter for the profile descriptions.
        env.add_extension(markdownExtension)

        # jinja2's tojson sorts keys in dicts by default.
        env.policies["json.dumps_kwargs"] = {"sort_keys": False}

        profile_form_template = env.get_template("form.html")

        return profile_form_template.render(profile_list=self.north_profiles)

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

        return options

    async def load_user_options(self):
        # The tool is chosen in the pre_spawn_hook. KubeSpawner would validate
        # user_options["profile"] against its own (empty) profile_list.
        pass

    def _get_profile(self, slug: str):
        """
        Returns the profile from north_profiles matching given slug.

        Raises an error if no profile exists for the given slug.
        """

        for profile in self.north_profiles:
            if profile.slug == slug:
                # return matching profile
                return profile

        raise ValueError(
            "No such profile: %s. Options include: %s"
            % (slug, ", ".join(p.slug for p in self.north_profiles))
        )

    @staticmethod
    def auth_state_hook(spawner, auth_state):
        if not spawner.name:
            return

        spawner.user_options["access_token"] = auth_state["access_token"]

    @default("pre_spawn_hook")
    def _pre_spawn_hook_default(self):
        return north_pre_spawn_hook


async def north_pre_spawn_hook(spawner):
    """Sets the tool image and mounts the user's NOMAD folders, on every spawn."""

    if spawner.name:
        if spawner.name not in [p.slug for p in spawner.north_profiles]:
            raise web.HTTPError(403, "This profile is not allowed")

        profile = spawner._get_profile(spawner.name)
        spawner.image = profile.image
        spawner.default_url = profile.default_url

    api_url = f"{spawner.nomad_api_url}/north/mounts/{spawner.name}"
    api_headers = {
        "Authorization": f"Bearer {spawner.user_options.get('access_token')}"
    }

    # requests is blocking; keep the hub's event loop free while NOMAD answers.
    response = await asyncio.to_thread(
        requests.get, api_url, headers=api_headers, timeout=spawner.nomad_api_timeout
    )
    response.raise_for_status()

    # NOMAD's mount sources are paths on the node, e.g. fs.north_home_external.
    volumes = []
    volume_mounts = []
    upload_ids = {}
    for i, mount in enumerate(response.json()):
        name = f"north-{i}"
        volumes.append({"name": name, "hostPath": {"path": mount["source"]}})
        volume_mounts.append(
            {
                "name": name,
                "mountPath": mount["target"],
                "readOnly": mount["mode"] != "rw",
            }
        )
        if "upload_id" in mount and mount["upload_id"] is not None:
            upload_ids[mount["upload_id"]] = mount["target"]

    # The spawner object is reused when a server is started again, so replace
    # the mounts of a previous spawn instead of adding to them.
    def without_north(items):
        return [item for item in items if not item["name"].startswith("north-")]

    spawner.volumes = [*without_north(spawner.volumes), *volumes]
    spawner.volume_mounts = [*without_north(spawner.volume_mounts), *volume_mounts]
    spawner.user_options["upload_ids"] = upload_ids

    # Access user identity or dynamic context
    username = spawner.user.name

    api_url = f"{spawner.nomad_api_url}/auth/app_token"
    api_headers = {
        "Authorization": f"Bearer {spawner.user_options.get('access_token')}"
    }

    # requests is blocking; keep the hub's event loop free while NOMAD answers.
    response = await asyncio.to_thread(
        requests.get,
        api_url,
        params={"expires_in": spawner.nomad_app_token_expires_in},
        headers=api_headers,
        timeout=spawner.nomad_api_timeout,
    )
    response.raise_for_status()

    access_token = response.json().get("app_token")

    # Set standard pod environment variables
    spawner.environment["NOMAD_CLIENT_USER"] = username
    spawner.environment["NOMAD_CLIENT_ACCESS_TOKEN"] = access_token
    spawner.environment["NOMAD_CLIENT_URL"] = spawner.nomad_api_url


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


c.JupyterHub.spawner_class = NORTHKubeSpawner  # noqa: F821
c.JupyterHub.user_redirect_hook = user_redirect_hook  # noqa: F821
c.JupyterHub.logo_file = f"{north_dir}/logo/nomad_logo.svg"  # noqa: F821

# The spawner calls the NOMAD API with the user's access token from the auth state.
c.Authenticator.enable_auth_state = True  # noqa: F821
c.Authenticator.refresh_pre_spawn = True  # noqa: F821

# Users do not have permission to read their own auth state by default, but auth_state is where the access_token is stored.
# https://oauthenticator.readthedocs.io/en/latest/how-to/refresh.html#refreshing-tokens-from-user-sessions
c.JupyterHub.load_roles.extend(
    [  # noqa: F821
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
)

# NOMAD manages the tools through the hub API as this service: it reads and
# creates users, starts and stops their named servers and follows the spawn
# progress. The deployment provides the token, e.g. the Helm chart from a Secret.
c.JupyterHub.services.append(  # noqa: F821
    {
        "name": "nomad-service",
        "api_token": os.environ.get("NOMAD_NORTH_HUB_SERVICE_API_TOKEN"),
    }
)
c.JupyterHub.load_roles.append(  # noqa: F821
    {
        "name": "nomad-service",
        "scopes": ["admin:users", "admin:servers"],
        "services": ["nomad-service"],
    }
)

# Every user of the NOMAD deployment may use its tools.
c.OAuthenticator.allow_all = True  # noqa: F821
