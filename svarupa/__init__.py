"""Svarupa: a verified map of a codebase.

Every node and every edge in every diagram carries file:line evidence, or it
does not render. Not a heuristic guess, not a model's plausible story: a claim
you can click through to the source line that proves it.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

try:
    __version__ = _version("svarupa")
except PackageNotFoundError:  # running from a source tree without install
    __version__ = "0.0.0+unknown"
