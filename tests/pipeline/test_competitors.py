from ideafinder.pipeline.competitors import aliases_for


def test_spellings_and_domains_share_an_alias():
    names = ["Jobber", "jobber.com", "GetJobber", "https://www.getjobber.com/pricing"]
    alias_sets = [set(aliases_for(n)) for n in names]
    assert all("jobber" in a for a in alias_sets)


def test_generic_things_are_not_competitors():
    for name in ["spreadsheet", "Email", "pen and paper", "API", "x"]:
        assert aliases_for(name) == [], name


def test_multi_word_names_keep_both_forms():
    assert set(aliases_for("Google Sheets")) >= {"google sheets", "googlesheets"}
