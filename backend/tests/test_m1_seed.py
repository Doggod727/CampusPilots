from app.scripts.seed_ai_knowledge import CONFIGS, DEMO_KB_ID


def test_m1_seed_is_complete_and_contains_no_credentials():
    assert len(CONFIGS)==9 and str(DEMO_KB_ID)=="61000000-0000-4000-8000-000000000001"
    serialized=repr(CONFIGS).lower()
    assert all(word not in serialized for word in ("api_key","password","token","secret"))
