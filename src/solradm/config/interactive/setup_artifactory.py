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

    token = ""
    while token == "":
        token = Prompt.ask("[question]Enter your Artifactory access token -> ", password=True).strip()

    return {
        "url": url,
        "maven_repo": maven_repo,
        "generic_repo": generic_repo,
        "token": token,
    }
