from doip_shared import signing

SECRET = "link-secret"
NOW = 1_000_000


def _sig(qid="Q1", component="data.parquet", exp=NOW + 600):
    return signing.sign(SECRET, qid, component, exp)


def test_valid_signature_verifies():
    assert signing.verify(SECRET, "Q1", "data.parquet", NOW + 600, _sig(), now=NOW)


def test_exp_may_be_a_string_and_qid_is_case_insensitive():
    assert signing.verify(SECRET, "q1", "data.parquet", str(NOW + 600), _sig(), now=NOW)


def test_expired_signature_is_rejected():
    assert not signing.verify(SECRET, "Q1", "data.parquet", NOW + 600, _sig(), now=NOW + 601)


def test_signature_is_bound_to_component_object_and_expiry():
    sig = _sig()
    assert not signing.verify(SECRET, "Q1", "other.parquet", NOW + 600, sig, now=NOW)
    assert not signing.verify(SECRET, "Q2", "data.parquet", NOW + 600, sig, now=NOW)
    assert not signing.verify(SECRET, "Q1", "data.parquet", NOW + 900, sig, now=NOW)


def test_wrong_secret_missing_secret_and_garbage_are_rejected():
    sig = _sig()
    assert not signing.verify("other", "Q1", "data.parquet", NOW + 600, sig, now=NOW)
    assert not signing.verify(None, "Q1", "data.parquet", NOW + 600, sig, now=NOW)
    assert not signing.verify("", "Q1", "data.parquet", NOW + 600, sig, now=NOW)
    assert not signing.verify(SECRET, "Q1", "data.parquet", "soon", sig, now=NOW)
    assert not signing.verify(SECRET, "Q1", "data.parquet", NOW + 600, None, now=NOW)
    assert not signing.verify(SECRET, "Q1", "data.parquet", NOW + 600, "", now=NOW)

def test_signature_matches_the_ckan_theme_test_vector():
    # Same vector as tests/test_signing.py in ckanext-episerve-theme.
    assert signing.sign("link-secret", "Q1", "data.parquet", 1000600) == "e90b05f212fb9c17a2cad863061dfbf3aba1830fdb1ba5b946744f35b3b7d1d2"
