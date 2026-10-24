"""Hermes plugin entrypoint."""


def register(ctx):
    from .live_location.plugin import register as register_native
    register_native(ctx)
