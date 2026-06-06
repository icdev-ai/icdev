# CUI // SP-CTI
# Namespace shim: make icdev/apps/ a package that spans BOTH icdev/apps/ AND
# the real project-root apps/ directory.  Without this, icdev/tools/__init__.py
# prepends icdev/ to sys.path and 'import apps.forge_academy' resolves to
# icdev/apps/forge_academy/ (which only has ai_gameday, autonomous_coder, …).
# extend_path adds every "apps/" directory found in sys.path to __path__, so
# 'apps.forge_academy' → project_root/apps/forge_academy/ is found correctly.
from pkgutil import extend_path
__path__ = extend_path(__path__, __name__)
