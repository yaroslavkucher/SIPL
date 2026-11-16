from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
VALID = sorted(EXAMPLES.glob("*.sipl"))
INVALID = sorted((EXAMPLES / "invalid").glob("*.sipl"))
GOLDEN = ROOT / "tests" / "golden"

POWER = """begin
 R := 1;
 while Y > 0 do
 begin
  R := R * X;
  Y := Y - 1
 end
end"""


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


@pytest.fixture
def power_source() -> str:
    return POWER
