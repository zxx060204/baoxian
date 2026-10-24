"""Source checkout launcher for the acquisition package."""
import sys
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from insurance_rag.acquisition.__main__ import main
    main()
