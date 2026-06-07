from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("telejournal")
except PackageNotFoundError:
    __version__ = "dev"  # Fallback for uninstalled/development use
