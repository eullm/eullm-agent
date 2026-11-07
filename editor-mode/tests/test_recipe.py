"""Recipes (format "recipe"): the ingredients and the steps are sentences
with their sources like the rest of the article, shown as lists, and the
site gets the recipe card search engines read (schema.org Recipe)."""

import json
from datetime import UTC, datetime

import httpx
import respx
from sqlalchemy import select, text

from editor import delivery, drafts, models as m, publishing as pub, seo, sitegit, translate as tr
from editor.core_client import CoreClient
from test_drafts import accepted_proposal, chat

PAGE = """<html><head><title>Carbonara</title></head><body><article>
<p>La carbonara per 4 persone: 320 g di spaghetti, 150 g di guanciale, 4 tuorli e 50 g di pecorino romano.</p>
<p>Si taglia il guanciale a listarelle e si rosola in padella senza olio finché diventa croccante.</p>
<p>Gli spaghetti cuociono in acqua salata per 10 minuti, poi si mantecano fuori dal fuoco con i tuorli e il pecorino.</p>
</article></body></html>"""


def recipe_doc(first, **over):
    doc = {"title": "Carbonara: la ricetta con guanciale e pecorino", "subtitle": "Quattro ingredienti e nessuna panna",
           "sections": [{"heading": None, "claims": [
               {"text": "La carbonara si fa con guanciale, tuorli e pecorino romano, senza panna.", "sources": [first]},
               {"text": "Il guanciale va reso croccante prima di unirlo alla pasta.", "sources": [first]}]}],
           "recipe": {"yield": "4 persone", "prep_minutes": 15, "cook_minutes": 10,
                      "ingredients": [{"text": "320 g di spaghetti", "sources": [first]},
                                      {"text": "150 g di guanciale", "sources": [first]},
                                      {"text": "4 tuorli", "sources": [first]},
                                      {"text": "50 g di pecorino romano", "sources": [first]}],
                      "steps": [{"text": "Taglia il guanciale a listarelle e rosolalo in padella senza olio.",
                                 "sources": [first]},
                                {"text": "Cuoci gli spaghetti in acqua salata per 10 minuti.", "sources": [first]},
                                {"text": "Lontano dal fuoco, manteca la pasta con i tuorli e il pecorino.",
                                 "sources": [first]}]}}
    doc.update(over)
    return doc


def test_a_recipe_needs_its_ingredients_and_steps_with_sources():
    doc = recipe_doc(7)
    a = drafts.parse_article(doc, {7}, "it")
    assert a.recipe["ingredients"] == "Ingredienti" and a.recipe["steps"] == "Procedimento"
    assert [c.section for c in a.claims].count("Ingredienti") == 4 and a.claims[-1].section == "Procedimento"
    assert drafts.parse_article(doc, {7}, "de").recipe["steps"] == "Zubereitung"
    # One ingredient is not a recipe: the article stays, the card does not.
    half = recipe_doc(7, recipe={"ingredients": [{"text": "uova", "sources": [7]}], "steps": []})
    half["sections"][0]["claims"].append({"text": "Il pecorino va grattugiato fine.", "sources": [7]})
    assert drafts.parse_article(half, {7}, "it").recipe is None
    # An ingredient without a source is refused like any other sentence.
    bare = recipe_doc(7)
    bare["recipe"]["ingredients"][0]["sources"] = []
    try:
        drafts.parse_article(bare, {7}, "it")
    except ValueError as e:
        assert "ingredient without valid sources" in str(e)
    else:
        raise AssertionError("an unsourced ingredient was taken")


def test_times_and_servings_stay_only_when_the_sources_give_them():
    src = {7: drafts.Source(7, "Carbonara", "https://news.example/1", "news", "",
                            "Per 4 persone. La pasta cuoce 10 minuti; il ragù 2 ore.", None)}
    claims = [drafts.Claim("x", [7], "Ingredienti"), drafts.Claim("y", [7], "Procedimento")]
    meta = {"ingredients": "Ingredienti", "steps": "Procedimento", "yield": "4 persone",
            "prep_minutes": 15, "cook_minutes": 10}
    out = drafts.recipe_meta(meta, src, claims)
    assert out["yield"] == "4 persone" and out["cook_minutes"] == 10 and out["prep_minutes"] is None
    assert drafts.recipe_meta(dict(meta, cook_minutes=120), src, claims)["cook_minutes"] == 120  # "2 ore"
    assert drafts.recipe_meta(dict(meta, **{"yield": "6 porzioni"}), src, claims)["yield"] is None


def test_the_card_and_its_schema():
    assert seo.duration(90) == "PT1H30M" and seo.duration(10) == "PT10M" and seo.duration(None) is None
    claims = [drafts.Claim("320 g di spaghetti", [1], "Ingredienti"), drafts.Claim("4 tuorli", [1], "Ingredienti"),
              drafts.Claim("Cuoci la pasta.", [1], "Procedimento")]
    card = seo.recipe_card({"ingredients": "Ingredienti", "steps": "Procedimento", "yield": "4 persone",
                            "prep_minutes": 15, "cook_minutes": 10}, claims)
    assert card == {"yield": "4 persone", "prepTime": "PT15M", "cookTime": "PT10M", "totalTime": "PT25M",
                    "ingredients": ["320 g di spaghetti", "4 tuorli"], "steps": ["Cuoci la pasta."]}
    assert seo.recipe_card({"ingredients": "Ingredienti", "steps": "Procedimento"}, claims[:1]) is None
    ld = seo.jsonld(title="Carbonara", description="d", language="it", url=None, keyphrase="carbonara",
                    published="2026-11-07", site_name="Ricette", site_url="https://ricette.example/",
                    citations=[], faq=[], recipe=card)
    r = next(x for x in ld if x["@type"] == "Recipe")
    assert r["recipeIngredient"] == card["ingredients"] and r["recipeYield"] == "4 persone"
    assert r["recipeInstructions"] == [{"@type": "HowToStep", "position": 1, "text": "Cuoci la pasta."}]
    assert all(x["@type"] != "Recipe" for x in seo.jsonld(title="t", description="d", language="it", url=None,
                                                        keyphrase="", published=None, site_name=None, site_url=None,
                                                        citations=[], faq=[]))


def write_recipe(db, tenant):
    pid, cited = accepted_proposal(db, tenant)
    with db.tenant(tenant) as s:
        s.execute(text("UPDATE editor.proposals SET format = 'recipe' WHERE id = :p"), {"p": pid})
    first = cited[0]
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://(news|rete)\.example/.*").mock(
            return_value=httpx.Response(200, text=PAGE, headers={"content-type": "text/html"}))
        router.get(url__regex=r".*/robots\.txt").mock(return_value=httpx.Response(404))
        router.get(url__regex=r".*/\.well-known/tdmrep\.json").mock(return_value=httpx.Response(404))
        llm = router.post("http://core.test/v1/llm/chat").mock(return_value=chat(json.dumps(recipe_doc(first))))
        with httpx.Client() as client:
            did = drafts.write_draft(db, tenant, pid, CoreClient("http://core.test", "tok"), client)
        prompt = json.loads(llm.calls[0].request.content)["messages"][-1]["content"]
    return did, prompt


def test_a_recipe_draft_has_its_lists_and_the_site_gets_its_card(db, tenant):
    did, prompt = write_recipe(db, tenant)
    assert "This article is a recipe" in prompt
    with db.tenant(tenant) as s:
        d = s.execute(select(m.drafts).where(m.drafts.c.id == did)).one()
        fields = pub.search_fields(s, d)
        fed = delivery.json_feed([delivery.article(s, did)], "ricette.example", "https://x/feed.json")
    assert d.flags == [], d.flags
    assert fed["items"][0]["_redazia"]["recipe"] == fields["recipe"]
    assert "## Ingredienti\n\n- 320 g di spaghetti[^1]\n- 150 g di guanciale" in d.body_md
    assert "## Procedimento\n\n1. Taglia il guanciale" in d.body_md and "\n3. Lontano dal fuoco" in d.body_md
    # 15 minutes of preparation are not in the source: not published.
    assert d.seo["recipe"]["prep_minutes"] is None and d.seo["recipe"]["cook_minutes"] == 10
    card = fields["recipe"]
    assert card["yield"] == "4 persone" and card["cookTime"] == "PT10M" and "prepTime" not in card
    assert card["ingredients"][0] == "320 g di spaghetti" and len(card["steps"]) == 3
    assert any(x["@type"] == "Recipe" for x in fields["jsonld"])
    # In a GitHub site the card goes in the front matter, for the site's own schema.
    when = datetime(2026, 11, 7, tzinfo=UTC)
    fm = sitegit.front_matter(sitegit.Format(), {"title": d.title, **fields}, "carbonara", when, None, None)
    line = next(x for x in fm.splitlines() if x.startswith("recipe: "))
    assert json.loads(line[len("recipe: "):]) == card
    learned = sitegit.Format(keys=["title", "slug", "date"], roles={"title": "title", "slug": "slug", "date": "date"},
                             learned=True)
    assert "\nrecipe: {" in sitegit.front_matter(learned, {"title": d.title, **fields}, "carbonara", when, None, None)


def test_a_translated_recipe_keeps_its_card(db, tenant):
    did, _ = write_recipe(db, tenant)
    with db.tenant(tenant) as s:
        claims = drafts.stored_claims(s, did)
    english = {"title": "Carbonara: the recipe with guanciale and pecorino", "subtitle": "Four ingredients, no cream",
               "headings": ["Ingredients", "Method"],
               "sentences": ["Carbonara is made with guanciale, yolks and pecorino romano, without cream.",
                             "The guanciale must be crisp before it meets the pasta.",
                             "320 g of spaghetti", "150 g of guanciale", "4 egg yolks", "50 g of pecorino romano",
                             "Cut the guanciale into strips and brown it in a pan without oil.",
                             "Boil the spaghetti in salted water for 10 minutes.",
                             "Off the heat, toss the pasta with the yolks and the pecorino."],
               "seo": {"keyphrase": "carbonara", "title": "Carbonara recipe", "description": "x" * 130,
                       "slug": "carbonara"}}
    assert len(english["sentences"]) == len(claims)
    with respx.mock() as router:
        router.post("http://core.test/v1/llm/chat").mock(return_value=chat(json.dumps(english)))
        tid = tr.translate(db, tenant, did, "en", CoreClient("http://core.test", "tok"))
    with db.tenant(tenant) as s:
        t = s.execute(select(m.drafts).where(m.drafts.c.id == tid)).one()
        card = pub.search_fields(s, t)["recipe"]
    assert t.seo["recipe"]["ingredients"] == "Ingredients" and "## Method\n\n1. Cut the guanciale" in t.body_md
    assert card["ingredients"][1] == "150 g of guanciale" and card["cookTime"] == "PT10M"
