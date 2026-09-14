from importlib.metadata import version

from jusi import __version__
from jusi.plugins.vd import catalog_entry


def test_bundled_provider_version_matches_distribution():
    assert __version__ == version("jusi")
    assert catalog_entry()["plugin_version"] == version("jusi")
