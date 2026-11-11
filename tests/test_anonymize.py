from app.anonymize import Pseudonymizer, _letters


def make():
    p = Pseudonymizer()
    p.alias("Wanderer.1234", "account")
    p.alias("Zoe Lightfoot", "character")
    p.alias("Bob", "character")
    p.alias("Knights of Ascalon", "guild")
    return p


def test_aliases_are_stable():
    p = make()
    assert p.alias("Bob", "character") == "Character_B"
    assert p.alias("zoe lightfoot", "character") == "Character_A"
    assert p.real("Character_A") == "Zoe Lightfoot"
    assert p.real("Character_Z") is None


def test_scrub_replaces_names_case_insensitive():
    p = make()
    text = "wanderer.1234 aka Wanderer plays ZOE LIGHTFOOT and Bob in Knights of Ascalon. Bobby stays."
    assert p.scrub(text) == "Account_1 aka Account_1 plays Character_A and Character_B in Guild_A. Bobby stays."


def test_scrub_redacts_keys_and_uuids():
    key = "ABCDEF01-2345-6789-ABCD-EF0123456789ABCDEF01-2345-6789-ABCD-EF0123456789"
    assert make().scrub(f"my key {key} and guild 4BBB52AA-D768-4FC6-8EDE-C299F2822F0F") == (
        "my key [redacted] and guild [id]"
    )


def test_restore_round_trip():
    p = make()
    text = "Character_A and Character_B in Guild_A. Character_AB is unknown."
    assert p.restore(text) == "Zoe Lightfoot and Bob in Knights of Ascalon. Character_AB is unknown."
    assert p.scrub(p.restore(text)) == text


def test_letters():
    assert [_letters(n) for n in (0, 25, 26, 27)] == ["A", "Z", "AA", "AB"]
