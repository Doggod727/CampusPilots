import json
from pathlib import Path


def test_frozen_rag_dataset_has_30_unique_safe_cases():
    path=Path(__file__).parent/"fixtures"/"m1_rag_frozen_30.jsonl"
    rows=[json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows)==30 and len({row["id"] for row in rows})==30
    assert sum(row["fallback"] for row in rows)>=5
    assert all("password" not in json.dumps(row,ensure_ascii=False).lower() for row in rows)
