"""SCU 公开数据快照的隐私与溯源守护测试（#198）。"""

import json
import re
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parents[1] / "app" / "scripts" / "data" / "scu"
PLACEHOLDER_PHONE = "028-00000000"
PATTERNS = {
    "mobile": re.compile(r"1[3-9]\d{9}"),
    "landline": re.compile(r"\b0\d{2,3}-\d{7,8}\b"),
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "id_card": re.compile(r"\b\d{17}[\dXx]\b"),
}


def _snapshot_files():
    files = [SNAPSHOT / "organizations.json", SNAPSHOT / "seed_data.json", SNAPSHOT / "knowledge_docs.json"]
    files.extend(sorted((SNAPSHOT / "docs").glob("*.md")))
    return files


def test_snapshot_files_exist_and_are_nonempty():
    files = _snapshot_files()
    assert len(files) == 3 + 12
    for path in files:
        assert path.is_file() and path.stat().st_size > 0, path


def test_snapshot_contains_no_personal_sensitive_patterns():
    for path in _snapshot_files():
        text = path.read_text(encoding="utf-8").replace(PLACEHOLDER_PHONE, "")
        for name, pattern in PATTERNS.items():
            assert not pattern.search(text), f"{name} found in {path.name}"


def test_knowledge_docs_all_have_source_url():
    catalog = json.loads((SNAPSHOT / "knowledge_docs.json").read_text(encoding="utf-8"))
    documents = catalog.get("documents", catalog if isinstance(catalog, list) else [])
    assert len(documents) == 12
    for document in documents:
        assert document.get("source_url", "").startswith("https://www.scu.edu.cn/")
        body = (SNAPSHOT / document["file"]).read_text(encoding="utf-8")
        assert "source_url:" in body


def test_seed_data_references_only_public_facts_and_placeholder_contacts():
    data = json.loads((SNAPSHOT / "seed_data.json").read_text(encoding="utf-8"))
    rendered = json.dumps(data, ensure_ascii=False)
    assert PLACEHOLDER_PHONE in rendered
    for name, pattern in PATTERNS.items():
        assert not pattern.search(rendered.replace(PLACEHOLDER_PHONE, "")), name
