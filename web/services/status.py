"""What the application calls itself, and where it is running."""

from __future__ import annotations

from dataclasses import dataclass

from web.config import Config

APPLICATION_NAME = "MOSAIQ"
APPLICATION_TAGLINE = "Retail segmentation platform"


@dataclass(frozen=True)
class ApplicationStatus:
    """The state the landing page reports."""

    name: str
    tagline: str
    environment: str


def application_status(config: Config) -> ApplicationStatus:
    """Describe the running application from its configuration."""
    return ApplicationStatus(
        name=APPLICATION_NAME,
        tagline=APPLICATION_TAGLINE,
        environment=config.environment,
    )
