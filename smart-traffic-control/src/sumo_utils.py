#!/usr/bin/env python3
"""
sumo_utils.py -- locate SUMO and its Python tools on any platform.

SUMO can be installed two different ways and this project supports both:

  A) Official installer (recommended on Windows).
     Sets the SUMO_HOME environment variable, e.g.
     C:\\Program Files (x86)\\Eclipse\\Sumo

  B) pip install eclipse-sumo
     Ships the binaries inside the Python package. No SUMO_HOME needed.

Every other script imports from here, so you never have to hard-code a path.
"""

import os
import shutil
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def project_root():
    """Absolute path to the smart-traffic-control/ folder."""
    return _PROJECT_ROOT


def path(*parts):
    """Build a path relative to the project root (works from any cwd)."""
    return os.path.join(_PROJECT_ROOT, *parts)


def sumo_home():
    """Return the SUMO installation directory, or None if not found."""
    if os.environ.get("SUMO_HOME") and os.path.isdir(os.environ["SUMO_HOME"]):
        return os.environ["SUMO_HOME"]
    try:
        import sumo  # provided by `pip install eclipse-sumo`
        return os.path.dirname(sumo.__file__)
    except ImportError:
        return None


def tools_dir():
    """Directory containing randomTrips.py etc."""
    home = sumo_home()
    if home is None:
        return None
    candidate = os.path.join(home, "tools")
    return candidate if os.path.isdir(candidate) else None


def ensure_tools_on_path():
    """Make `import traci` / `import sumolib` work even without pip installs."""
    t = tools_dir()
    if t and t not in sys.path:
        sys.path.append(t)


def binary(name):
    """
    Resolve a SUMO executable ('sumo', 'sumo-gui', 'netgenerate',
    'netconvert', 'duarouter') to a full path.
    """
    found = shutil.which(name)
    if found:
        return found

    home = sumo_home()
    if home:
        for sub in ("bin", ""):
            for ext in ("", ".exe"):
                cand = os.path.join(home, sub, name + ext)
                if os.path.isfile(cand):
                    return cand

    raise FileNotFoundError(
        f"Could not find the SUMO executable '{name}'.\n"
        "Fix it by doing ONE of the following:\n"
        "  1. Install SUMO from https://sumo.dlr.de/docs/Downloads.php and set\n"
        "     SUMO_HOME (Windows: setx SUMO_HOME \"C:\\Program Files (x86)\\Eclipse\\Sumo\")\n"
        "  2. Run:  pip install eclipse-sumo\n"
    )


def random_trips_py():
    """Full path to randomTrips.py."""
    t = tools_dir()
    if t is None:
        raise FileNotFoundError(
            "SUMO tools directory not found. Set SUMO_HOME or run "
            "`pip install eclipse-sumo`."
        )
    p = os.path.join(t, "randomTrips.py")
    if not os.path.isfile(p):
        raise FileNotFoundError(f"randomTrips.py not found at {p}")
    return p


def describe():
    """Print a diagnostic report. Run this first if anything misbehaves."""
    print("=" * 60)
    print("SUMO ENVIRONMENT CHECK")
    print("=" * 60)
    print(f"project root : {project_root()}")
    print(f"SUMO_HOME    : {sumo_home() or 'NOT FOUND'}")
    print(f"tools dir    : {tools_dir() or 'NOT FOUND'}")
    for exe in ("sumo", "sumo-gui", "netgenerate", "duarouter"):
        try:
            print(f"{exe:12s} : {binary(exe)}")
        except FileNotFoundError:
            print(f"{exe:12s} : NOT FOUND")
    ensure_tools_on_path()
    for mod in ("traci", "sumolib"):
        try:
            __import__(mod)
            print(f"{mod:12s} : import OK")
        except ImportError:
            print(f"{mod:12s} : IMPORT FAILED  (pip install {mod})")
    print("=" * 60)


if __name__ == "__main__":
    describe()
