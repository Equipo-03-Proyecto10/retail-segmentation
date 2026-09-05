"""The status service answers without a request, which is the layer's point."""

from web.config import Config
from web.services.status import (
    APPLICATION_NAME,
    APPLICATION_TAGLINE,
    application_status,
)


def test_reports_the_product_identity_and_the_configured_environment() -> None:
    status = application_status(
        Config(
            secret_key="unused-here",
            environment="production",
            port=5000,
            log_level="INFO",
        )
    )

    assert status.name == APPLICATION_NAME
    assert status.tagline == APPLICATION_TAGLINE
    assert status.environment == "production"
