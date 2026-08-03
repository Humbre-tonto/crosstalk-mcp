import sys
from pathlib import Path

python_dir = Path(__file__).parent.parent / "python"
sys.path.insert(0, str(python_dir))
