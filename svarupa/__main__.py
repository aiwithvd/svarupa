"""`python -m svarupa` is `svarupa` (review #20 S10: the documented shape
of running a module failed with no `__main__`)."""

import sys

from svarupa.cli import main

if __name__ == "__main__":  # pragma: no cover - the CLI is tested through main()
    sys.exit(main())
