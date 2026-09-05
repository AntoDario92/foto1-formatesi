"""Build the always-on public landing page for Render's static CDN."""
import shutil
from pathlib import Path

from app import Site, legal, public_landing

ROOT = Path(__file__).parent
OUT = ROOT / "static_public"
BACKEND = "https://formatesi.onrender.com"


class PublicRequest:
    method = "GET"
    user = None
    data = {}


def absolute_app_links(document):
    for path in ("/registrati", "/login", "/anteprima"):
        document = document.replace(f'href="{path}"', f'href="{BACKEND}{path}"')
    wake = f"<script>fetch('{BACKEND}/health',{{mode:'no-cors'}}).catch(()=>{{}})</script>"
    return document.replace("</body>", wake + "</body>")


def write_page(path, title, body):
    target = OUT / path
    target.mkdir(parents=True, exist_ok=True)
    html = Site({}).page(PublicRequest(), title, body)
    (target / "index.html").write_text(absolute_app_links(html), encoding="utf-8")


if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir()
shutil.copytree(ROOT / "static", OUT / "static")
write_page(Path("."), "La tua tesi comincia a prendere forma", public_landing([]))
write_page(Path("privacy"), "Privacy", legal(Site({}), "/privacy"))
write_page(Path("condizioni"), "Condizioni del servizio", legal(Site({}), "/condizioni"))
