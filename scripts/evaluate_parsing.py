"""Run real-material parsing evaluation from a source checkout."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"src"))
from insurance_rag.parsing.evaluation import main

if __name__ == "__main__":
    raise SystemExit(main())
