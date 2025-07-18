from __future__ import annotations

import importlib.metadata
import logging
import os
import sys
from typing import IO, Any

import click
from django.conf import settings

from sentry.silo.patches.silo_aware_transaction_patch import patch_silo_aware_atomic
from sentry.utils import warnings
from sentry.utils.arroyo import initialize_arroyo_main
from sentry.utils.sdk import configure_sdk
from sentry.utils.warnings import DeprecatedSettingWarning


class ConfigurationError(ValueError, click.ClickException):
    def show(self, file: IO[str] | None = None) -> None:
        if file is None:
            from click._compat import get_text_stderr

            file = get_text_stderr()
        click.secho(f"!! Configuration error: {self!r}", file=file, fg="red")


def register_plugins(settings: Any, raise_on_plugin_load_failure: bool = False) -> None:
    from sentry.plugins.base import plugins

    # entry_points={
    #    'sentry.plugins': [
    #         'example = sentry_plugins.example.plugin:ExamplePlugin'
    #     ],
    # },
    entry_points = {
        ep
        for dist in importlib.metadata.distributions()
        for ep in dist.entry_points
        if ep.group == "sentry.plugins"
    }

    for ep in entry_points:
        try:
            plugin = ep.load()
        except Exception:
            import traceback

            click.echo(f"Failed to load plugin {ep.name!r}:\n{traceback.format_exc()}", err=True)
            if raise_on_plugin_load_failure:
                raise
        else:
            plugins.register(plugin)

    for plugin in plugins.all(version=None):
        init_plugin(plugin)

    from sentry.integrations.manager import default_manager as integrations
    from sentry.utils.imports import import_string

    for integration_path in settings.SENTRY_DEFAULT_INTEGRATIONS:
        try:
            integration_cls = import_string(integration_path)
        except Exception:
            import traceback

            click.echo(
                f"Failed to load integration {integration_path!r}:\n{traceback.format_exc()}",
                err=True,
            )
        else:
            integrations.register(integration_cls)

    for integration in integrations.all():
        try:
            integration.setup()
        except AttributeError:
            pass


def init_plugin(plugin: Any) -> None:
    from sentry.plugins.base import bindings

    plugin.setup(bindings)

    # Register contexts from plugins if necessary
    if hasattr(plugin, "get_custom_contexts"):
        from sentry.interfaces.contexts import contexttype

        for cls in plugin.get_custom_contexts() or ():
            contexttype(cls)

    if hasattr(plugin, "get_cron_schedule") and plugin.is_enabled():
        schedules = plugin.get_cron_schedule()
        if schedules:
            settings.CELERYBEAT_SCHEDULE.update(schedules)

    if hasattr(plugin, "get_worker_imports") and plugin.is_enabled():
        imports = plugin.get_worker_imports()
        if imports:
            settings.CELERY_IMPORTS += tuple(imports)

    if hasattr(plugin, "get_worker_queues") and plugin.is_enabled():
        from kombu import Queue

        for queue in plugin.get_worker_queues():
            try:
                name, routing_key = queue
            except ValueError:
                name = routing_key = queue
            q = Queue(name, routing_key=routing_key)
            q.durable = False
            settings.CELERY_QUEUES.append(q)


def initialize_receivers() -> None:
    # force signal registration
    import sentry.receivers  # NOQA


def get_asset_version(settings: Any) -> str:
    path = os.path.join(settings.STATIC_ROOT, "version")
    try:
        with open(path) as fp:
            return fp.read().strip()
    except OSError:
        from time import time

        return str(int(time()))


# Options which must get extracted into Django settings while
# bootstrapping. Everything else will get validated and used
# as a part of OptionsManager.
options_mapper = {
    # 'cache.backend': 'SENTRY_CACHE',
    # 'cache.options': 'SENTRY_CACHE_OPTIONS',
    # 'system.databases': 'DATABASES',
    # 'system.debug': 'DEBUG',
    "system.secret-key": "SECRET_KEY",
    "mail.backend": "EMAIL_BACKEND",
    "mail.host": "EMAIL_HOST",
    "mail.port": "EMAIL_PORT",
    "mail.username": "EMAIL_HOST_USER",
    "mail.password": "EMAIL_HOST_PASSWORD",
    "mail.use-tls": "EMAIL_USE_TLS",
    "mail.use-ssl": "EMAIL_USE_SSL",
    "mail.from": "SERVER_EMAIL",
    "mail.subject-prefix": "EMAIL_SUBJECT_PREFIX",
    "github-login.client-id": "GITHUB_APP_ID",
    "github-login.client-secret": "GITHUB_API_SECRET",
    "github-login.require-verified-email": "GITHUB_REQUIRE_VERIFIED_EMAIL",
    "github-login.base-domain": "GITHUB_BASE_DOMAIN",
    "github-login.api-domain": "GITHUB_API_DOMAIN",
    "github-login.extended-permissions": "GITHUB_EXTENDED_PERMISSIONS",
    "github-login.organization": "GITHUB_ORGANIZATION",
}


def bootstrap_options(settings: Any, config: str | None = None) -> None:
    """
    Quickly bootstrap options that come in from a config file
    and convert options into Django settings that are
    required to even initialize the rest of the app.
    """
    # Make sure our options have gotten registered
    from sentry.options import load_defaults

    load_defaults()

    if config is not None:
        # Attempt to load our config yaml file
        from yaml.parser import ParserError
        from yaml.scanner import ScannerError

        from sentry.utils.yaml import safe_load

        try:
            with open(config, "rb") as fp:
                options = safe_load(fp)
        except OSError:
            # Gracefully fail if yaml file doesn't exist
            options = {}
        except (AttributeError, ParserError, ScannerError) as e:
            raise ConfigurationError("Malformed config.yml file: %s" % str(e))

        # Empty options file, so fail gracefully
        if options is None:
            options = {}
        # Options needs to be a dict
        elif not isinstance(options, dict):
            raise ConfigurationError("Malformed config.yml file")
    else:
        options = {}

    from sentry.conf.server import DEAD

    # First move options from settings into options
    for k, v in options_mapper.items():
        if getattr(settings, v, DEAD) is not DEAD and k not in options:
            warnings.warn(DeprecatedSettingWarning(options_mapper[k], "SENTRY_OPTIONS['%s']" % k))
            options[k] = getattr(settings, v)

    # Stuff everything else into SENTRY_OPTIONS
    # these will be validated later after bootstrapping
    for k, v in options.items():
        settings.SENTRY_OPTIONS[k] = v

    # Now go back through all of SENTRY_OPTIONS and promote
    # back into settings. This catches the case when values are defined
    # only in SENTRY_OPTIONS and no config.yml file
    for o in (settings.SENTRY_DEFAULT_OPTIONS, settings.SENTRY_OPTIONS):
        for k, v in o.items():
            if k in options_mapper:
                # Map the mail.backend aliases to something Django understands
                if k == "mail.backend":
                    try:
                        v = settings.SENTRY_EMAIL_BACKEND_ALIASES[v]
                    except KeyError:
                        pass
                # Escalate the few needed to actually get the app bootstrapped into settings
                setattr(settings, options_mapper[k], v)


def configure_structlog() -> None:
    """
    Make structlog comply with all of our options.
    """
    import logging.config

    import structlog
    from django.conf import settings

    from sentry import options
    from sentry.logging import LoggingFormat

    kwargs: dict[str, Any] = {
        "wrapper_class": structlog.stdlib.BoundLogger,
        "cache_logger_on_first_use": True,
        "processors": [
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.format_exc_info,
        ],
    }

    fmt_from_env = os.environ.get("SENTRY_LOG_FORMAT")
    if fmt_from_env:
        settings.SENTRY_OPTIONS["system.logging-format"] = fmt_from_env.lower()

    fmt = options.get("system.logging-format")

    if fmt == LoggingFormat.HUMAN:
        from sentry.logging.handlers import HumanRenderer

        kwargs["processors"].extend(
            [structlog.processors.ExceptionPrettyPrinter(), HumanRenderer()]
        )
    elif fmt == LoggingFormat.MACHINE:
        from sentry.logging.handlers import JSONRenderer

        kwargs["processors"].append(JSONRenderer())

    is_s4s = os.environ.get("CUSTOMER_ID") == "sentry4sentry"
    if is_s4s:
        kwargs["logger_factory"] = structlog.PrintLoggerFactory(sys.stderr)

    structlog.configure(**kwargs)

    if is_s4s:
        logging.info("Writing logs to stderr. Expected only in s4s")

    lvl = os.environ.get("SENTRY_LOG_LEVEL")

    if lvl and lvl not in logging._nameToLevel:
        raise AttributeError("%s is not a valid logging level." % lvl)

    settings.LOGGING["root"].update({"level": lvl or settings.LOGGING["default_level"]})

    if lvl:
        for logger in settings.LOGGING["overridable"]:
            try:
                settings.LOGGING["loggers"][logger].update({"level": lvl})
            except KeyError:
                raise KeyError("%s is not a defined logger." % logger)

    logging.config.dictConfig(settings.LOGGING)


def show_big_error(message: str | list[str]) -> None:
    if isinstance(message, str):
        lines = message.strip().splitlines()
    else:
        lines = message
    maxline = max(map(len, lines))
    click.echo("", err=True)
    click.secho("!!!{}!!!".format("!" * min(maxline, 80)), err=True, fg="red")
    click.secho("!! %s !!" % "".center(maxline), err=True, fg="red")
    for line in lines:
        click.secho("!! %s !!" % line.center(maxline), err=True, fg="red")
    click.secho("!! %s !!" % "".center(maxline), err=True, fg="red")
    click.secho("!!!{}!!!".format("!" * min(maxline, 80)), err=True, fg="red")
    click.echo("", err=True)


def initialize_app(config: dict[str, Any], skip_service_validation: bool = False) -> None:
    settings = config["settings"]

    # Just reuse the integration app for Single Org / Self-Hosted as
    # it doesn't make much sense to use 2 separate apps for SSO and
    # integration.
    if settings.SENTRY_SINGLE_ORGANIZATION:
        options_mapper.update(
            {
                "github-app.client-id": "GITHUB_APP_ID",
                "github-app.client-secret": "GITHUB_API_SECRET",
            }
        )

    bootstrap_options(settings, config["options"])

    logging.raiseExceptions = settings.DEBUG

    configure_structlog()

    # Commonly setups don't correctly configure themselves for production envs
    # so lets try to provide a bit more guidance
    if settings.CELERY_ALWAYS_EAGER and not settings.DEBUG:
        warnings.warn(
            "Sentry is configured to run asynchronous tasks in-process. "
            "This is not recommended within production environments. "
            "See https://develop.sentry.dev/services/queue/ for more information."
        )

    if settings.SENTRY_SINGLE_ORGANIZATION:
        settings.SENTRY_FEATURES["organizations:create"] = False

    if not hasattr(settings, "SUDO_COOKIE_SECURE"):
        settings.SUDO_COOKIE_SECURE = getattr(settings, "SESSION_COOKIE_SECURE", False)
    if not hasattr(settings, "SUDO_COOKIE_DOMAIN"):
        settings.SUDO_COOKIE_DOMAIN = getattr(settings, "SESSION_COOKIE_DOMAIN", None)
    if not hasattr(settings, "SUDO_COOKIE_PATH"):
        settings.SUDO_COOKIE_PATH = getattr(settings, "SESSION_COOKIE_PATH", "/")

    if not hasattr(settings, "CSRF_COOKIE_SECURE"):
        settings.CSRF_COOKIE_SECURE = getattr(settings, "SESSION_COOKIE_SECURE", False)
    if not hasattr(settings, "CSRF_COOKIE_DOMAIN"):
        settings.CSRF_COOKIE_DOMAIN = getattr(settings, "SESSION_COOKIE_DOMAIN", None)
    if not hasattr(settings, "CSRF_COOKIE_PATH"):
        settings.CSRF_COOKIE_PATH = getattr(settings, "SESSION_COOKIE_PATH", "/")

    for key in settings.CACHES:
        if not hasattr(settings.CACHES[key], "VERSION"):
            settings.CACHES[key]["VERSION"] = 2

    settings.ASSET_VERSION = get_asset_version(settings)
    settings.STATIC_URL = settings.STATIC_URL.format(version=settings.ASSET_VERSION)

    monkeypatch_drf_listfield_serializer_errors()

    import django

    django.setup()

    validate_regions(settings)

    validate_outbox_config()

    monkeypatch_django_migrations()

    patch_silo_aware_atomic()

    apply_legacy_settings(settings)

    bind_cache_to_option_store()

    register_plugins(settings)

    initialize_receivers()

    validate_options(settings)

    validate_snuba()

    configure_sdk()

    setup_services(validate=not skip_service_validation)

    import_grouptype()

    initialize_arroyo_main()

    # Hacky workaround to dynamically set the CSRF_TRUSTED_ORIGINS for self hosted
    if settings.SENTRY_SELF_HOSTED and not settings.CSRF_TRUSTED_ORIGINS:
        from sentry import options

        system_url_prefix = options.get("system.url-prefix")
        if system_url_prefix:
            settings.CSRF_TRUSTED_ORIGINS = [system_url_prefix]
        else:
            # For first time users that have not yet set system url prefix, let's default to localhost url
            settings.CSRF_TRUSTED_ORIGINS = ["http://localhost:9000", "http://127.0.0.1:9000"]


def setup_services(validate: bool = True) -> None:
    from sentry import (
        analytics,
        buffer,
        digests,
        newsletter,
        nodestore,
        quotas,
        ratelimits,
        search,
        tagstore,
        tsdb,
    )

    service_list = (
        analytics,
        buffer,
        digests,
        newsletter,
        nodestore,
        quotas,
        ratelimits,
        search,
        tagstore,
        tsdb,
    )

    for service in service_list:
        if validate:
            try:
                service.validate()
            except AttributeError as e:
                raise ConfigurationError(
                    f"{service.__name__} service failed to call validate()\n{e}"
                ).with_traceback(e.__traceback__)
        try:
            service.setup()
        except AttributeError as e:
            if not hasattr(service, "setup") or not callable(service.setup):
                raise ConfigurationError(
                    f"{service.__name__} service failed to call setup()\n{e}"
                ).with_traceback(e.__traceback__)
            raise


def validate_options(settings: Any) -> None:
    from sentry.options import default_manager

    default_manager.validate(settings.SENTRY_OPTIONS, warn=True)


def validate_regions(settings: Any) -> None:
    from sentry.types.region import load_from_config

    if not settings.SENTRY_REGION_CONFIG:
        return

    load_from_config(settings.SENTRY_REGION_CONFIG).validate_all()


def monkeypatch_django_migrations() -> None:
    # This monkeypatches django's migration executor with our own, which
    # adds some small but important customizations.
    from sentry.new_migrations.monkey import monkey_migrations

    monkey_migrations()


def monkeypatch_drf_listfield_serializer_errors() -> None:
    # This patches reverts https://github.com/encode/django-rest-framework/pull/5655,
    # effectively we don't get that slight improvement
    # in serializer error structure introduced in drf 3.8.x,
    # This is simply the fastest way forward, otherwise
    # frontend and sentry-cli needs updating and people using
    # myriad other custom api clients may complain if we break
    # their error handling.
    # We're mainly focused on getting to Python 3.8, so this just isn't worth it.

    from collections.abc import Mapping

    from rest_framework.fields import ListField
    from rest_framework.utils import html

    def to_internal_value(self: ListField, data: Any) -> Any:
        if html.is_html_input(data):
            data = html.parse_html_list(data, default=[])
        if isinstance(data, (str, Mapping)) or not hasattr(data, "__iter__"):
            self.fail("not_a_list", input_type=type(data).__name__)
        if not self.allow_empty and len(data) == 0:
            self.fail("empty")
        # Begin code retained from < drf 3.8.x.
        return [self.child.run_validation(item) for item in data]
        # End code retained from < drf 3.8.x.

    ListField.to_internal_value = to_internal_value  # type: ignore[assignment,method-assign]

    # We don't need to patch DictField since we don't use it
    # at the time of patching. This is fine since anything newly
    # introduced that does use it should prefer the better serializer
    # errors.


def bind_cache_to_option_store() -> None:
    # The default ``OptionsStore`` instance is initialized without the cache
    # backend attached. The store itself utilizes the cache during normal
    # operation, but can't use the cache before the options (which typically
    # includes the cache configuration) have been bootstrapped from the legacy
    # settings and/or configuration values. Those options should have been
    # loaded at this point, so we can plug in the cache backend before
    # continuing to initialize the remainder of the application.
    from django.core.cache import cache as default_cache

    from sentry.options import default_store

    default_store.set_cache_impl(default_cache)


def apply_legacy_settings(settings: Any) -> None:
    from sentry import options

    # SENTRY_USE_QUEUE used to determine if Celery was eager or not
    if hasattr(settings, "SENTRY_USE_QUEUE"):
        warnings.warn(
            DeprecatedSettingWarning(
                "SENTRY_USE_QUEUE",
                "CELERY_ALWAYS_EAGER",
                "https://develop.sentry.dev/services/queue/",
            )
        )
        settings.CELERY_ALWAYS_EAGER = not settings.SENTRY_USE_QUEUE

    for old, new in (
        ("SENTRY_ADMIN_EMAIL", "system.admin-email"),
        ("SENTRY_SYSTEM_MAX_EVENTS_PER_MINUTE", "system.rate-limit"),
        ("SENTRY_ENABLE_EMAIL_REPLIES", "mail.enable-replies"),
        ("SENTRY_SMTP_HOSTNAME", "mail.reply-hostname"),
        ("MAILGUN_API_KEY", "mail.mailgun-api-key"),
        ("SENTRY_FILESTORE", "filestore.backend"),
        ("SENTRY_FILESTORE_OPTIONS", "filestore.options"),
        ("SENTRY_RELOCATION_BACKEND", "filestore.relocation-backend"),
        ("SENTRY_RELOCATION_OPTIONS", "filestore.relocation-options"),
        ("SENTRY_PROFILES_BACKEND", "filestore.profiles-backend"),
        ("SENTRY_PROFILES_OPTIONS", "filestore.profiles-options"),
        ("GOOGLE_CLIENT_ID", "auth-google.client-id"),
        ("GOOGLE_CLIENT_SECRET", "auth-google.client-secret"),
    ):
        if new not in settings.SENTRY_OPTIONS and hasattr(settings, old):
            warnings.warn(DeprecatedSettingWarning(old, "SENTRY_OPTIONS['%s']" % new))
            settings.SENTRY_OPTIONS[new] = getattr(settings, old)

    if hasattr(settings, "SENTRY_REDIS_OPTIONS"):
        if "redis.clusters" in settings.SENTRY_OPTIONS:
            raise Exception(
                "Cannot specify both SENTRY_OPTIONS['redis.clusters'] option and SENTRY_REDIS_OPTIONS setting."
            )
        else:
            warnings.warn(
                DeprecatedSettingWarning(
                    "SENTRY_REDIS_OPTIONS",
                    'SENTRY_OPTIONS["redis.clusters"]',
                    removed_in_version="8.5",
                )
            )
            settings.SENTRY_OPTIONS["redis.clusters"] = {"default": settings.SENTRY_REDIS_OPTIONS}
    else:
        # Provide backwards compatibility to plugins expecting there to be a
        # ``SENTRY_REDIS_OPTIONS`` setting by using the ``default`` cluster.
        # This should be removed when ``SENTRY_REDIS_OPTIONS`` is officially
        # deprecated. (This also assumes ``FLAG_NOSTORE`` on the configuration
        # option.)
        settings.SENTRY_REDIS_OPTIONS = options.get("redis.clusters")["default"]

    if settings.TIME_ZONE != "UTC":
        # non-UTC timezones are not supported
        show_big_error("TIME_ZONE should be set to UTC")

    # Set ALLOWED_HOSTS if it's not already available
    if not settings.ALLOWED_HOSTS:
        settings.ALLOWED_HOSTS = ["*"]

    if hasattr(settings, "SENTRY_ALLOW_REGISTRATION"):
        warnings.warn(
            DeprecatedSettingWarning(
                "SENTRY_ALLOW_REGISTRATION", 'SENTRY_FEATURES["auth:register"]'
            )
        )
        settings.SENTRY_FEATURES["auth:register"] = settings.SENTRY_ALLOW_REGISTRATION

    settings.DEFAULT_FROM_EMAIL = settings.SENTRY_OPTIONS.get(
        "mail.from", settings.SENTRY_DEFAULT_OPTIONS.get("mail.from")
    )

    # HACK(mattrobenolt): This is a one-off assertion for a system.secret-key value.
    # If this becomes a pattern, we could add another flag to the OptionsManager to cover this, but for now
    # this is the only value that should prevent the app from booting up. Currently FLAG_REQUIRED is used to
    # trigger the Installation Wizard, not abort startup.
    if not settings.SENTRY_OPTIONS.get("system.secret-key"):
        raise ConfigurationError(
            "`system.secret-key` MUST be set. Use 'sentry config generate-secret-key' to get one."
        )


def validate_snuba() -> None:
    """
    Make sure everything related to Snuba is in sync.

    This covers a few cases:

    * When you have features related to Snuba, you must also
      have Snuba fully configured correctly to continue.
    * If you have Snuba specific search/tagstore/tsdb backends,
      you must also have a Snuba compatible eventstream backend
      otherwise no data will be written into Snuba.
    * If you only have Snuba related eventstream, yell that you
      probably want the other backends otherwise things are weird.
    """
    if not settings.DEBUG:
        return

    has_all_snuba_required_backends = (
        settings.SENTRY_SEARCH
        in (
            "sentry.search.snuba.EventsDatasetSnubaSearchBackend",
            "sentry.utils.services.ServiceDelegator",
        )
        and settings.SENTRY_TAGSTORE == "sentry.tagstore.snuba.SnubaTagStorage"
        and
        # TODO(mattrobenolt): Remove ServiceDelegator check
        settings.SENTRY_TSDB
        in ("sentry.tsdb.redissnuba.RedisSnubaTSDB", "sentry.utils.services.ServiceDelegator")
    )

    eventstream_is_snuba = (
        settings.SENTRY_EVENTSTREAM == "sentry.eventstream.snuba.SnubaEventStream"
        or settings.SENTRY_EVENTSTREAM == "sentry.eventstream.kafka.KafkaEventStream"
    )

    # All good here, it doesn't matter what else is going on
    if has_all_snuba_required_backends and eventstream_is_snuba:
        return

    if not eventstream_is_snuba:
        show_big_error(
            """
It appears that you are requiring Snuba,
but your SENTRY_EVENTSTREAM is not compatible.

Current settings:

SENTRY_SEARCH = %r
SENTRY_TAGSTORE = %r
SENTRY_TSDB = %r
SENTRY_EVENTSTREAM = %r

See: https://github.com/getsentry/snuba#sentry--snuba"""
            % (
                settings.SENTRY_SEARCH,
                settings.SENTRY_TAGSTORE,
                settings.SENTRY_TSDB,
                settings.SENTRY_EVENTSTREAM,
            )
        )
        raise ConfigurationError("Cannot continue without Snuba configured correctly.")

    if eventstream_is_snuba and not has_all_snuba_required_backends:
        show_big_error(
            """
You are using a Snuba compatible eventstream
without configuring search/tagstore/tsdb also to use Snuba.
This is probably not what you want.

Current settings:

SENTRY_SEARCH = %r
SENTRY_TAGSTORE = %r
SENTRY_TSDB = %r
SENTRY_EVENTSTREAM = %r

See: https://github.com/getsentry/snuba#sentry--snuba"""
            % (
                settings.SENTRY_SEARCH,
                settings.SENTRY_TAGSTORE,
                settings.SENTRY_TSDB,
                settings.SENTRY_EVENTSTREAM,
            )
        )


def validate_outbox_config() -> None:
    from sentry.hybridcloud.models.outbox import ControlOutboxBase, RegionOutboxBase

    for outbox_name in settings.SENTRY_OUTBOX_MODELS["CONTROL"]:
        ControlOutboxBase.from_outbox_name(outbox_name)

    for outbox_name in settings.SENTRY_OUTBOX_MODELS["REGION"]:
        RegionOutboxBase.from_outbox_name(outbox_name)


def import_grouptype() -> None:
    from sentry.issues.grouptype import import_grouptype

    import_grouptype()
