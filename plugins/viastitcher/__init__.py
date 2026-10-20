# Vendored ViaStitcher by Michele Santucci (MIT, see LICENSE) - https://github.com/weirdgyn/viastitcher
# Upstream commit da665278aa7a019424e638f16fe26452de291bfe (2026-10-14).
# Only change vs upstream: this __init__ does not register a toolbar button; MyPlugin's hub launches it.
from . import localization
from .viastitcher_plugin import ViaStitcherPlugin
