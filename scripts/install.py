"""One-command installer from a reviewed source release or GitHub checkout."""
import runpy
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
scripts = root / "skills/litscout-browser-download/scripts"
sys.path.insert(0, str(scripts))
sys.argv[1:1] = ["--source-root", str(root), "--install-skill"]
runpy.run_path(str(scripts / "setup.py"), run_name="__main__")
