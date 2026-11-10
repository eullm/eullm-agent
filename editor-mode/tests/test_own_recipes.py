"""A recipe site (Francesco, 2026-11-08): Redazia sees by itself that the
site publishes recipes, takes the news as the idea only and writes the
site's own recipe, with every field of its recipe cards, a photograph of
the dish and no sources on the page; on a site kept in a repository the
recipe goes in the recipes' folder, as a JSON file shaped like the others."""

import json
import socket
from datetime import UTC, datetime

import httpx
import respx
from sqlalchemy import select, text

from editor import autopilot, drafts, images, models as m, profile as prof, publishing as pub, seo, sitegit
from editor.core_client import CoreClient
from test_drafts import accepted_proposal, chat
from test_publishing import _send, _target

PAGE = """<html><head><title>Carbonara</title></head><body><article>
<p>Sui social torna la carbonara: i video con il guanciale croccante sono tra i più visti della settimana.</p>
<p>Gli chef discutono di panna e di uova intere.</p>
</article></body></html>"""

OWN = {"servings": 4, "prep_minutes": 10, "cook_minutes": 15, "difficulty": "medium", "category": "I Primi",
       "tags": ["Veloce", "tradizione romana"],
       "ingredients": [{"quantity": "350", "unit": "g", "name": "spaghetti"},
                       {"quantity": "150", "unit": "g", "name": "guanciale"},
                       {"quantity": "4", "unit": "", "name": "tuorli"},
                       {"quantity": "q.b.", "unit": "", "name": "pepe nero"}],
       "steps": ["Rosola il guanciale a listarelle senza olio finché è croccante.",
                 "Cuoci gli spaghetti in acqua salata e scolali al dente.",
                 "Fuori dal fuoco unisci i tuorli sbattuti con il pecorino e un mestolo di acqua di cottura."],
       "nutrition": {"calories": "540 kcal", "protein": "24g", "carbs": "62g", "fat": "21g", "sugars": "2g",
                     "servingWeight": 260}}


def doc(first, **over):
    d = {"title": "Carbonara cremosa con guanciale croccante", "subtitle": "Il primo romano che spopola sui social",
         "sections": [{"heading": None, "claims": [
             {"text": "La carbonara è tornata tra i piatti più cercati della settimana.", "sources": [first]}]}],
         "seo": {"keyphrase": "carbonara cremosa", "slug": "carbonara-cremosa"}, "recipe": OWN}
    d.update(over)
    return d


def recipe_site(db, tenant):
    pid, cited = accepted_proposal(db, tenant)
    with db.tenant(tenant) as s:
        s.execute(text("UPDATE editor.proposals SET format = 'recipe' WHERE id = :p"), {"p": pid})
        site_id = s.execute(text("SELECT site_id FROM editor.proposals WHERE id = :p"), {"p": pid}).scalar()
        assert prof.mark_recipe_site(s, site_id, ["I Primi", "I Secondi", "I Dessert"])
    return pid, cited, site_id


def write(db, tenant, pid, first, answer=None):
    with respx.mock(assert_all_called=False) as router:
        router.get(url__regex=r"https://(news|rete)\.example/.*").mock(
            return_value=httpx.Response(200, text=PAGE, headers={"content-type": "text/html"}))
        router.get(url__regex=r".*/robots\.txt").mock(return_value=httpx.Response(404))
        router.get(url__regex=r".*/\.well-known/tdmrep\.json").mock(return_value=httpx.Response(404))
        llm = router.post("http://core.test/v1/llm/chat").mock(
            return_value=chat(json.dumps(answer or doc(first))))
        with httpx.Client() as client:
            did = drafts.write_draft(db, tenant, pid, CoreClient("http://core.test", "tok"), client)
        prompt = json.loads(llm.calls[0].request.content)["messages"][-1]["content"]
    return did, prompt


def test_a_recipe_site_is_told_by_its_settings_or_its_sector():
    assert autopilot.recipe_site({"settings": {"site_kind": "recipes"}})
    assert not autopilot.recipe_site({"settings": {"site_kind": "articles"}, "sector": {"value": "Ricette"}})
    assert autopilot.recipe_site({"sector": {"value": "Ricette della cucina italiana"}})
    assert autopilot.recipe_site({"summary": {"value": "A food blog with easy recipes."}})
    assert not autopilot.recipe_site({"sector": {"value": "Arredamento cucine"}})
    assert autopilot.RECIPE_SITE in autopilot.brief_lines({"settings": {"site_kind": "recipes"}})
    assert autopilot.RECIPE_SITE not in autopilot.brief_lines({"sector": {"value": "Tecnologia"}})


def test_the_site_own_recipe_is_checked_and_clamped():
    r = drafts.parse_own_recipe({**OWN, "servings": "4", "difficulty": "hard", "tags": ["Senza glutine", "keto", "keto"]},
                                "it")
    assert r["own"] and r["servings"] == 4 and r["difficulty"] == "Difficile" and r["tags"] == ["senza-glutine", "keto"]
    assert r["items"][0] == {"quantity": "350", "unit": "g", "name": "spaghetti"} and len(r["method"]) == 3
    assert r["nutrition"]["servingWeight"] == 260 and r["category"] == "I Primi"
    import pytest

    with pytest.raises(ValueError, match="ingredients"):
        drafts.parse_own_recipe({"ingredients": [{"name": "uova"}], "steps": ["x"]}, "it")
    # The opening rests on the items; the recipe cites nothing.
    article = drafts.parse_article(doc(7), {7}, "it", own=True)
    assert len(article.claims) == 1 and article.recipe["items"][1]["name"] == "guanciale"


def test_a_recipe_site_gets_its_own_recipe_with_its_card(db, tenant):
    pid, cited, site_id = recipe_site(db, tenant)
    did, prompt = write(db, tenant, pid, cited[0])
    assert autopilot.RECIPE_SITE in prompt and "a recipe of the site, its own" in prompt
    assert "Every ingredient, quantity" not in prompt  # not the recipe told by the items
    with db.tenant(tenant) as s:
        d = s.execute(select(m.drafts).where(m.drafts.c.id == did)).one()
        fields = pub.search_fields(s, d)
    drafts.decide(db, tenant, did, True, "francesco")
    with db.tenant(tenant) as s:
        payload = pub.build_payload(s, s.execute(select(m.drafts).where(m.drafts.c.id == did)).one())
    assert d.flags == [], d.flags
    assert d.seo["section"] == "I Primi" and d.seo["recipe"]["own"]
    assert "## Ingredienti\n\n- 350 g spaghetti\n- 150 g guanciale" in d.body_md
    assert "## Procedimento\n\n1. Rosola il guanciale" in d.body_md and "\n3. Fuori dal fuoco" in d.body_md
    card = fields["recipe"]
    assert card["servings"] == 4 and card["prep_minutes"] == 10 and card["prepTime"] == "PT10M"
    assert card["difficulty"] == "Medio" and card["tags"] == ["veloce", "tradizione-romana"]
    assert card["ingredients"][0] == "350 g spaghetti" and card["ingredient_list"][3]["quantity"] == "q.b."
    ld = next(x for x in fields["jsonld"] if x["@type"] == "Recipe")
    assert ld["nutrition"]["calories"] == "540 kcal" and ld["recipeCategory"] == "I Primi"
    # No sources on the pages of a recipe site: they stay in the newsroom.
    assert "[^1]" not in payload["markdown"] and "## Fonti" not in payload["markdown"]
    assert "[^1]" in d.body_md
    # Its photograph is the dish.
    with db.tenant(tenant) as s:
        d = s.execute(select(m.drafts).where(m.drafts.c.id == did)).one()
    prompt_, _alt = images.describe(None, d, {"settings": {"cover_style": "impact"}})
    assert prompt_.startswith("Professional food photograph of the finished dish")


def test_a_category_the_site_does_not_have_is_made(db, tenant):
    pid, cited, _site = recipe_site(db, tenant)
    did, _ = write(db, tenant, pid, cited[0], doc(cited[0], recipe={**OWN, "category": "I Contorni"}))
    with db.tenant(tenant) as s:
        assert s.execute(select(m.drafts.c.seo).where(m.drafts.c.id == did)).scalar()["section"] == "I Contorni"
    _, prompt = write(db, tenant, pid, cited[0], doc(cited[0], recipe={**OWN, "category": "i primi"}))
    with db.tenant(tenant) as s:
        latest = s.execute(select(m.drafts.c.seo).order_by(m.drafts.c.id.desc()).limit(1)).scalar()
    assert latest["section"] == "I Primi"  # written as the site writes it


def test_ingredients_are_split_into_quantity_unit_and_name():
    assert sitegit.split_ingredient("320 g di spaghetti") == {"quantity": "320", "unit": "g", "name": "spaghetti"}
    assert sitegit.split_ingredient("2 cucchiai di olio") == {"quantity": "2", "unit": "cucchiai", "name": "olio"}
    assert sitegit.split_ingredient("4 tuorli") == {"quantity": "4", "unit": "", "name": "tuorli"}
    assert sitegit.split_ingredient("sale q.b.") == {"quantity": "q.b.", "unit": "", "name": "sale"}
    assert sitegit.split_ingredient("pepe nero") == {"quantity": "", "unit": "", "name": "pepe nero"}
    assert sitegit.split_ingredient("1,5 kg di patate") == {"quantity": "1,5", "unit": "kg", "name": "patate"}
    assert sitegit.split_ingredient("2 gamberi") == {"quantity": "2", "unit": "", "name": "gamberi"}


SITE_RECIPE = {"title": "Carbonara classica", "description": "La vera carbonara.", "servings": 4, "prepTime": 10,
               "cookTime": 15, "difficulty": "Medio", "category": "I Primi",
               "ingredients": [{"quantity": "350", "unit": "g", "name": "spaghetti"}],
               "steps": [{"description": "Cuoci."}], "image": "/images/ricette/Carbonara.webp", "tags": ["veloce"],
               "keywords": "carbonara, guanciale", "nutrition": {"calories": "520 kcal"}, "author": "La Grande Cucina",
               "recipeCuisine": "Italian", "date": "2024-03-01", "slug": "carbonara-classica"}
SITE_ARTICLE = {"title": "La reazione di Maillard", "slug": "maillard", "excerpt": "La crosticina.",
                "body": "## Cos'è\n\nTesto.", "image": "/images/blog/maillard.webp", "date": "2024-03-20",
                "tags": ["tecnica"]}


def test_a_json_post_copies_the_site_s_recipes():
    posts = [sitegit.Post("content/ricette/carbonara-classica.json",
                          [(k, json.dumps(v, ensure_ascii=False)) for k, v in SITE_RECIPE.items()],
                          "carbonara-classica", SITE_RECIPE),
             sitegit.Post("content/ricette/tiramisu.json",
                          [(k, json.dumps(v, ensure_ascii=False)) for k, v in
                           {**SITE_RECIPE, "title": "Tiramisù", "category": "I Dessert", "date": "2024-02-01"}.items()],
                          "tiramisu", {**SITE_RECIPE, "title": "Tiramisù"})]
    fmt = sitegit.learn(posts)
    assert fmt.ext == ".json" and sitegit.is_recipes(posts)
    card = seo.recipe_card(drafts.parse_own_recipe(OWN, "it"), [])
    payload = {"title": "Carbonara cremosa", "meta_description": "Cremosa e veloce.", "keyphrase": "carbonara cremosa",
               "section": "I Primi", "recipe": card, "language": "it"}
    out = json.loads(sitegit.json_post(fmt, payload, "carbonara-cremosa", datetime(2026, 11, 8, 9, tzinfo=UTC),
                                       "/images/ricette/carbonara-cremosa.jpg", "Un piatto di carbonara", "Testo."))
    assert list(out) == [k for k in SITE_RECIPE if k in out]  # the site's order
    assert out["ingredients"][0] == {"quantity": "350", "unit": "g", "name": "spaghetti"}
    assert out["steps"][0] == {"description": OWN["steps"][0]}
    assert out["servings"] == 4 and out["prepTime"] == 10 and out["cookTime"] == 15 and out["difficulty"] == "Medio"
    assert out["category"] == "I Primi" and out["date"] == "2026-11-08" and out["slug"] == "carbonara-cremosa"
    assert out["author"] == "La Grande Cucina" and out["recipeCuisine"] == "Italian"
    assert out["tags"] == ["veloce", "tradizione-romana"] and out["keywords"].startswith("carbonara cremosa, veloce")
    assert out["nutrition"]["servingWeight"] == 260 and out["description"] == "Cremosa e veloce."
    assert "body" not in out and "recipe" not in out
    # An article of the site: its body in Markdown, its excerpt.
    art = sitegit.learn([sitegit.Post("content/tecnica/maillard.json",
                                      [(k, json.dumps(v)) for k, v in SITE_ARTICLE.items()], "maillard", SITE_ARTICLE)])
    out = json.loads(sitegit.json_post(art, {"title": "Il sottovuoto", "meta_description": "Come.", "keyphrase": "sottovuoto"},
                                       "sottovuoto", datetime(2026, 11, 8, tzinfo=UTC), None, None, "## Perché\n\nTesto."))
    assert out == {"title": "Il sottovuoto", "slug": "sottovuoto", "excerpt": "Come.", "body": "## Perché\n\nTesto.",
                   "date": "2026-11-08", "tags": ["sottovuoto"]}
    assert sitegit.blocks("## A\n\nUno due.\n\n- x\n- y") == [
        {"type": "heading", "level": 2, "text": "A"}, {"type": "paragraph", "text": "Uno due."},
        {"type": "paragraph", "text": "x"}, {"type": "paragraph", "text": "y"}]


def test_github_sends_the_recipe_to_the_recipes_folder(db, tenant, monkeypatch):
    from github_fake import FakeGitHub

    pid, cited, site_id = recipe_site(db, tenant)
    did, _ = write(db, tenant, pid, cited[0])
    drafts.decide(db, tenant, did, True, "francesco")
    with db.tenant(tenant) as s:
        s.execute(text("INSERT INTO editor.draft_images (tenant_id, draft_id, token, prompt, alt, model, mime, width,"
                       " height, content) VALUES (:t, :d, 'tok', 'p', 'Carbonara nel piatto', 'm', 'image/jpeg', 10,"
                       " 10, :c)"), {"t": tenant, "d": did, "c": b"\xff\xd8jpeg"})
        s.execute(text("UPDATE editor.sites SET domain = 'lagrandecucina.it' WHERE id = :s"), {"s": site_id})
    target = _target(db, tenant, site_id, monkeypatch, path="content/tecnica", verify=False)
    gh = FakeGitHub({
        "content/tecnica/maillard.json": json.dumps(SITE_ARTICLE),
        "content/ricette/carbonara-classica.json": json.dumps(SITE_RECIPE),
        "content/ricette/tiramisu.json": json.dumps({**SITE_RECIPE, "title": "Tiramisù", "category": "I Dessert"}),
        "public/llms.txt": "# La Grande Cucina\n\n## Ricette\n\n- [Tutte](https://lagrandecucina.it/ricette)\n"})
    _pid, out = _send(db, tenant, did, target, "publish", gh)
    assert out["status"] == "published", out
    assert out["url"] == "https://lagrandecucina.it/ricette/carbonara-cremosa"
    post = json.loads(gh.text("content/ricette/carbonara-cremosa.json"))
    assert post["category"] == "I Primi" and post["ingredients"][1] == {"quantity": "150", "unit": "g", "name": "guanciale"}
    assert post["image"] == "/images/ricette/carbonara-cremosa.jpg"
    assert gh.files()["public/images/ricette/carbonara-cremosa.jpg"] == b"\xff\xd8jpeg"
    assert "content/tecnica/carbonara-cremosa.json" not in gh.files()
    assert "fonti" not in json.dumps(post).lower()


def test_no_sources_on_the_page():
    md = "# T\n\nUno.[^1] Due.[^2]\n\n_Etichetta AI_\n\n## Fonti\n\n[^1]: [a](https://a.example), A\n"
    assert drafts.without_sources(md) == "# T\n\nUno. Due.\n\n_Etichetta AI_\n"


def test_the_newsroom_sees_the_recipe_as_it_goes_out(db, tenant):
    """Francesco 2026-11-10: in the newsroom he saw only the opening, not the
    recipe card; the checks asked a recipe for sources and subheadings."""
    from test_ui import logged_in

    pid, cited, _site = recipe_site(db, tenant)
    did, prompt = write(db, tenant, pid, cited[0])
    assert "never speaks of an article, a blog" in prompt
    page = logged_in(db, tenant, "owner").get(f"/drafts/{did}").text
    assert "350 g</strong> spaghetti" in page and "Rosola il guanciale" in page
    assert "Porzioni" in page and "540 kcal" in page and "I Primi" in page
    with db.tenant(tenant) as s:
        checks = {x["key"]: x["ok"] for x in s.execute(select(m.drafts.c.seo).where(m.drafts.c.id == did)).scalar()["checks"]}
    assert "sources" not in checks and checks["headings"]


def test_one_outlet_does_not_give_every_proposal():
    """Francesco 2026-11-10: the day's recipes all came from Gustoblog."""
    from editor import picks

    def cand(i, source):
        return picks.Candidate(i, i, f"t{i}", "", source, None, False, 1, 0.9, None, None, 1.0, score=1 - i / 100)

    cands = [cand(i, "Gustoblog") for i in range(20)] + [cand(20 + i, f"Altro {i}") for i in range(5)]
    shown = picks.spread(cands, 30)[:12]
    assert sum(c.source == "Gustoblog" for c in shown) == 10 and len({c.source for c in shown}) == 3
    assert picks.source_cap(4) == 2 and picks.source_cap(6) == 2 and picks.source_cap(2) == 2


def test_a_recipe_site_mixes_the_season_with_the_news(db, tenant):
    from editor import evergreen

    _pid, _cited, site_id = recipe_site(db, tenant)
    with db.tenant(tenant) as s:
        body = s.execute(select(m.editorial_profiles.c.body).where(m.editorial_profiles.c.site_id == site_id)
                         .order_by(m.editorial_profiles.c.id.desc()).limit(1)).scalar()
    assert evergreen.mode_of(body) == "mixed"
    assert "Christmas in November" in evergreen.SEASON_SYSTEM
