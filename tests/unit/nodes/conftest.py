import sys
import types

from griptape.artifacts.url_artifact import UrlArtifact

# The engine's 3D parameter imports ThreeDUrlArtifact from the standard library, which the dev
# environment does not install. Stand in a minimal module so the fal nodes can be imported.
try:
    import griptape_nodes_library.three_d.three_d_artifact  # noqa: F401  # pyright: ignore[reportMissingImports]
except ImportError:

    class ThreeDUrlArtifact(UrlArtifact):
        pass

    for module_name in (
        "griptape_nodes_library",
        "griptape_nodes_library.three_d",
        "griptape_nodes_library.three_d.three_d_artifact",
    ):
        sys.modules.setdefault(module_name, types.ModuleType(module_name))
    sys.modules["griptape_nodes_library.three_d.three_d_artifact"].ThreeDUrlArtifact = ThreeDUrlArtifact  # pyright: ignore[reportAttributeAccessIssue]
