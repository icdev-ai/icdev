# Root-level shim so `import signal_generator` resolves from the project root.
# The actual implementation lives at tools/fathomdesk/signal_generator.py.
from tools.fathomdesk.signal_generator import *  # noqa: F401, F403
from tools.fathomdesk.signal_generator import load_thresholds, generate  # noqa: F401
