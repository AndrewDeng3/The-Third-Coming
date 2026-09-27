"""Console-free launcher (used by 'Start with Windows' and for double-click launching)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stickfigure.app import main  # noqa: E402

sys.exit(main())
