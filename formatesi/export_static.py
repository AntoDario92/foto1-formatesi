"""Build the always-on public landing page for Render's static CDN."""
import shutil
from pathlib import Path

from app import ATENEI_PAGES, Site, legal, public_landing, university_page

ROOT = Path(__file__).parent
OUT = ROOT / "static_public"
BACKEND = "https://formatesi.onrender.com"


class PublicRequest:
    method = "GET"
    user = None
    data = {}


def absolute_app_links(document):
    consultation = (
        '<a class="button small" href="https://wa.me/393505815735?text=Ciao%20FormaTesi%2C%20vorrei%20una%20consulenza%20per%20la%20mia%20tesi." '
        'target="_blank" rel="noopener">Consulenza gratuita <span aria-hidden="true">↗</span></a>'
    )
    portal_access = (
        f'<a href="{BACKEND}/login">Accedi</a>'
        f'<a class="button small" href="{BACKEND}/registrati">Paragrafo gratuito <span aria-hidden="true">↗</span></a>'
    )
    document = document.replace(consultation, portal_access)
    for path in ("/registrati", "/login", "/anteprima"):
        document = document.replace(f'href="{path}"', f'href="{BACKEND}{path}"')
    return document


def write_page(path, title, body):
    target = OUT / path
    target.mkdir(parents=True, exist_ok=True)
    html = Site({}).page(PublicRequest(), title, body)
    (target / "index.html").write_text(absolute_app_links(html), encoding="utf-8")


if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir()
shutil.copytree(ROOT / "static", OUT / "static")
write_page(Path("."), "La tua tesi comincia a prendere forma", public_landing([], True))
write_page(Path("privacy"), "Privacy", legal(Site({}), "/privacy"))
write_page(Path("condizioni"), "Condizioni del servizio", legal(Site({}), "/condizioni"))
for slug, name in ATENEI_PAGES.items():
    write_page(Path("atenei") / slug, "Supporto tesi " + name, university_page(slug, True))
