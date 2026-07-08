import rich
from rich.prompt import Prompt

DEFAULT_MAVEN_REPO = "monk-offline-maven"
DEFAULT_GENERIC_REPO = "monk-offline-generic"


def setup() -> dict:
    """Prompt for the Artifactory profile used by the `rt` subcommands.

    Returns a dict persisted under the `artifactory` config key, which seeds the
    default values of `sa rt upload` / `sa rt download`.
    """
    url = ""
    while url == "":
        url = Prompt.ask(
            "[question]Enter your Artifactory base URL (e.g. https://artifactory.internal/artifactory) -> "
        ).strip()

    maven_repo = Prompt.ask(
        "[question]Enter the target Maven repo key -> ", default=DEFAULT_MAVEN_REPO
    ).strip()
    generic_repo = Prompt.ask(
        "[question]Enter the target generic repo key (for the Gradle distribution) -> ",
        default=DEFAULT_GENERIC_REPO,
    ).strip()

    # Auth: token preferred; user+password is the fallback. Require at least one.
    token = user = password = ""
    while not (token or (user and password)):
        token = Prompt.ask(
            "[question]Enter your Artifactory access token (leave blank to use user/password) -> ",
            password=True, default="",
        ).strip()
        if not token:
            user = Prompt.ask(
                "[question]Enter your Artifactory username -> ", default=""
            ).strip()
            password = Prompt.ask(
                "[question]Enter your Artifactory password -> ", password=True, default=""
            ).strip()
        if not (token or (user and password)):
            rich.print("[error]❌ Configure an access token, or both a username and password.")

    return {
        "url": url,
        "maven_repo": maven_repo,
        "generic_repo": generic_repo,
        "token": token,
        "user": user,
        "password": password,
    }
