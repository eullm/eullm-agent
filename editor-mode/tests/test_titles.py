"""A title is the site's own (Francesco 2026-11-11): an outlet's headline is
not copied, but a dish keeps its traditional name."""

from editor import evergreen
from editor.text import copied_title


def test_a_headline_is_not_copied_but_the_dish_keeps_its_name():
    assert copied_title("Cotoletta alla milanese", ["Cotoletta alla milanese"]) is None  # the dish's name alone
    assert copied_title("Cotoletta alla milanese: la ricetta originale",
                        ["Cotoletta alla milanese, la ricetta originale"]) == "Cotoletta alla milanese, la ricetta originale"
    assert copied_title("Cotoletta alla milanese croccante, come la fa la nonna",
                        ["Cotoletta alla milanese: la ricetta originale"]) is None
    assert copied_title("Saltimbocca alla romana in 15 minuti", ["Saltimbocca alla romana: la ricetta tradizionale"]) is None


def test_a_theme_proposal_with_a_copied_headline_is_written_again():
    check = evergreen._validator({1, 2}, {1: 1, 2: 2}, ["Torta di mele e noci in 5 minuti: la ricetta autunnale"])
    base = {"angle": "", "why_now": "", "format": "recipe", "country": "", "section": "", "citations": [1, 2]}
    try:
        check(base | {"title": "Torta di mele e noci in 5 minuti: la ricetta autunnale!"})
        raise AssertionError("a copied headline passed")
    except ValueError as e:
        assert "traditional name" in str(e)
    assert check(base | {"title": "Torta di mele e noci, soffice e profumata per le merende d'autunno"})["citations"] == [1, 2]
