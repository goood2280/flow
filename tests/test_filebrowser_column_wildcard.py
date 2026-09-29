from routers import filebrowser as fb

COLS = ["root_lot_id", "wafer_id", "CD_TOP", "cd_mid", "temp_chuck", "rootXlot", "a.b", "QTIME_A_M3", "QTIME_M3"]


def _match(q):
    matcher = fb.column_name_matcher(q)
    return [c for c in COLS if matcher(c)]


def test_plain_term_is_case_insensitive_substring():
    assert _match("CD") == ["CD_TOP", "cd_mid"]
    assert _match("") == COLS


def test_star_and_percent_match_like_splittable_custom_list():
    # 부분일치: 와일드카드 앞뒤에 다른 글자가 있어도 된다.
    assert _match("QTIME*M3") == ["QTIME_A_M3", "QTIME_M3"]
    assert _match("temp*") == ["temp_chuck"]
    assert _match("cd%top") == ["CD_TOP"]
    assert _match("lot*id") == ["root_lot_id"]


def test_underscore_and_regex_chars_stay_literal():
    assert "rootXlot" not in _match("root_lot")
    assert _match("a.b") == ["a.b"]


def test_comma_separated_terms_are_or():
    assert _match("lot_id, wafer") == ["root_lot_id", "wafer_id"]
    assert _match("CD_TOP，temp") == ["CD_TOP", "temp_chuck"]
