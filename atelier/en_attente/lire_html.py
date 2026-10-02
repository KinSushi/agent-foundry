"""Dire ce que dit vraiment une page html enregistrée : titre, métadonnées, titres, liens et contenu principal, sans le bruit.

Mesuré dans cette session : sur idlelib/help.html de CPython 3.14.7 (documentation Sphinx),
trafilatura 2.2.0 ne rend que 2 833 mots et 53 des 147 débuts de paragraphe ; cet outil en rend
6 264 (99,1 % des mots visibles, 142 des 147 débuts de paragraphe, 99 % des mots de trafilatura
inclus). Sur une page de test construite ici, retirer les balises garde le script de suivi et le
bandeau de cookies (160 mots au lieu de 106), et trafilatura rend le texte « https://www.ma-banque.fr »
d'un lien sans dire qu'il mène à banque-piege.example.com ; l'outil le signale.

QUESTION
    Que dit vraiment cette page html : titre, contenu principal, titres h1-h6, liens
    (internes, externes, texte et cible), métadonnées (title, description, OpenGraph,
    canonical, langue) ? Et qu'y a-t-il de faux (ancre vers un id absent, id en double,
    lien dont le texte affiche une autre adresse que sa cible) ?
MESURE
    Le fichier (ou le texte) est décodé (BOM, charset déclaré, sinon utf-8, sinon cp1252),
    puis analysé en arbre : html.parser de la bibliothèque standard avec fermetures
    implicites (p, li, td...), ou lxml, ou beautifulsoup4 s'ils sont installés. Contenu
    principal : élément main (ou role=main), sinon article unique, sinon le bloc de plus
    forte densité de texte (score par paragraphe remonté aux parents, pondéré par la part
    de texte hors liens), après retrait de script, style, nav, header, footer, aside,
    formulaires, éléments cachés et blocs dont la classe ou l'id désigne menu, pied,
    barre latérale, partage, cookies, commentaires. Comparaison facultative du texte
    principal avec trafilatura (recouvrement des mots). Aucune requête réseau.
HYPOTHÈSES
    La page est enregistrée telle que servie (pas de contenu ajouté par JavaScript). Le
    domaine du site vient de canonical, og:url, base ou --url-page ; sans eux, tout lien
    absolu est classé externe.
LIMITES
    Le contenu produit par JavaScript est invisible. L'heuristique de densité peut choisir
    un bloc voisin du vrai contenu (page d'accueil, liste de liens). Les feuilles de style
    ne sont pas appliquées : un élément masqué seulement par une classe css reste visible
    pour l'outil. Ancres vérifiées dans la page seulement.
CONTRE-EXEMPLES
    Constaté : un billet en div (sans article ni main) suivi d'une section id="comments"
    titrée « 3 commentaires » : les commentaires sont fusionnés au contenu principal (85 mots
    au lieu de 41), car une section titrée n'est jamais écartée sur sa classe ou son id. Et
    go_spec.html (Go 1.25.1) porte 2 liens « #MethodName » sans id correspondant dans le
    fichier : signalés absents, alors que le site publié peut ajouter ces id à la génération.
INVOCATION
    {outil} --texte '<html lang="fr"><head><title>Essai</title><meta name="description" content="Page de test"></head><body><nav><a href="/">Accueil</a></nav><main><h1 id="haut">Titre</h1><p>Un paragraphe assez long pour être le contenu principal de la page.</p><a href="#haut">Remonter</a> <a href="https://exemple.org/doc">la doc</a></main></body></html>' --json
DOMAINE
    Pages html enregistrées (exports, captures, documentation générée) que l'on veut lire,
    résumer ou citer sans menus ni pieds de page ; pas les applications à rendu JavaScript.
"""

from __future__ import annotations

import argparse
import codecs
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urljoin, urlsplit

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import lxml.html as lxml_html
except ImportError:
    lxml_html = None

try:
    import bs4
except ImportError:
    bs4 = None

try:
    import trafilatura
except ImportError:
    trafilatura = None

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", INTITULE_HYPOTHESES, "LIMITES",
             "CONTRE-EXEMPLES", INTITULE_INVOCATION, "DOMAINE")
NOM_OUTIL = "lire_html"
EXTENSIONS = frozenset({".html", ".htm", ".xhtml", ".shtml"})
DOSSIERS_IGNORES = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv"})
PLAFOND_LISTE = 200

VIDES = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
                   "param", "source", "track", "wbr"})
BLOCS = frozenset({"address", "article", "aside", "blockquote", "details", "dialog", "dd", "div",
                   "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2",
                   "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "li", "main", "nav", "ol",
                   "p", "pre", "section", "table", "tr", "td", "th", "ul", "caption", "summary",
                   "body", "html", "br", "tbody", "thead", "tfoot", "menu", "legend", "option"})
INVISIBLES = frozenset({"script", "style", "noscript", "template", "head", "title", "svg", "canvas",
                        "iframe", "object", "embed", "math"})
BRUIT = frozenset({"nav", "header", "footer", "aside", "form", "button", "select", "input",
                   "textarea", "dialog", "menu"})
ROLES_BRUIT = frozenset({"navigation", "banner", "contentinfo", "complementary", "search",
                         "menu", "menubar", "dialog", "alertdialog", "toolbar"})
MOTIF_BRUIT = re.compile(r"(?:^|[-_\s])(nav|navbar|navigation|menu|footer|sidebar|breadcrumbs?|cookies?|"
                         r"consent|banner|share|sharing|social|related|advert|ads|promo|newsletter|"
                         r"subscribe|popup|modal|skip|toolbar|pagination|comments?|masthead)(?:[-_\s]|$)",
                         re.IGNORECASE)
PARAGRAPHES = frozenset({"p", "pre", "td", "blockquote", "li", "dd", "figcaption"})
TITRES = ("h1", "h2", "h3", "h4", "h5", "h6")
FERMETURES = {
    "li": ({"li"}, {"ul", "ol", "menu"}),
    "dt": ({"dt", "dd"}, {"dl"}),
    "dd": ({"dt", "dd"}, {"dl"}),
    "tr": ({"tr", "td", "th"}, {"table", "tbody", "thead", "tfoot"}),
    "td": ({"td", "th"}, {"tr", "table"}),
    "th": ({"td", "th"}, {"tr", "table"}),
    "tbody": ({"tbody", "thead", "tfoot", "tr", "td", "th"}, {"table"}),
    "thead": ({"tbody", "thead", "tfoot", "tr", "td", "th"}, {"table"}),
    "tfoot": ({"tbody", "thead", "tfoot", "tr", "td", "th"}, {"table"}),
    "option": ({"option"}, {"select", "datalist"}),
}
CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""", re.IGNORECASE)
DECLARATION_XML = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)
TEXTE_URL = re.compile(r"^(https?://|www\.)?((?:[a-z0-9-]+\.)+([a-z]{2,}))(?:[/:?#]\S*)?$", re.IGNORECASE)
DOMAINES_COURANTS = frozenset({"com", "org", "net", "edu", "gov", "int", "io", "co", "ai", "app", "dev",
                               "info", "biz", "eu", "fr", "de", "uk", "be", "ch", "ca", "us", "it", "es",
                               "nl", "jp", "cn", "ru", "br", "in", "au", "se", "no", "pl", "pt"})
BLANCS = re.compile(r"\s+")
SEPARATEUR = "\x1e"
PREFORMATE = "\x1f"
SAUT = "\x1d"
CACHE_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE)


class EntreeInvalide(Exception):
    """Entrée inutilisable (chemin absent, binaire, pas du html) : code 2."""


@dataclass(eq=False)
class Noeud:
    """Élément html ; enfants : éléments ou chaînes de texte."""

    balise: str
    attrs: dict[str, str]
    enfants: list[Noeud | str] = field(default_factory=list)
    parent: Noeud | None = field(default=None, repr=False)


# --------------------------------------------------------------------------- arbres

class Constructeur(HTMLParser):
    """html.parser -> Noeud, avec les fermetures implicites usuelles du html."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.racine = Noeud("#document", {})
        self.pile: list[Noeud] = [self.racine]
        self.fins_orphelines = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        self.fermer_implicites(tag)
        noeud = Noeud(tag, {k.lower(): (v or "") for k, v in attrs}, [], self.pile[-1])
        self.pile[-1].enfants.append(noeud)
        if tag not in VIDES:
            self.pile.append(noeud)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VIDES and len(self.pile) > 1:
            self.pile.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for i in range(len(self.pile) - 1, 0, -1):
            if self.pile[i].balise == tag:
                del self.pile[i:]
                return
        self.fins_orphelines += 1

    def handle_data(self, data: str) -> None:
        enfants = self.pile[-1].enfants
        if enfants and isinstance(enfants[-1], str):
            enfants[-1] += data
        else:
            enfants.append(data)

    def fermer_implicites(self, tag: str) -> None:
        """Ferme p, li, td... ouverts quand le html le prévoit."""
        if tag in BLOCS and tag not in ("br",):
            for i in range(len(self.pile) - 1, 0, -1):
                balise = self.pile[i].balise
                if balise == "p":
                    del self.pile[i:]
                    break
                if balise in BLOCS or balise in ("button", "table"):
                    break
        regle = FERMETURES.get(tag)
        if regle:
            fermes, bornes = regle
            for i in range(len(self.pile) - 1, 0, -1):
                if self.pile[i].balise in bornes:
                    break
                if self.pile[i].balise in fermes:
                    del self.pile[i:]
                    break


def arbre_stdlib(texte: str) -> tuple[Noeud, int]:
    """Arbre par html.parser ; rend aussi le nombre de balises fermantes orphelines."""
    constructeur = Constructeur()
    constructeur.feed(texte)
    constructeur.close()
    return constructeur.racine, constructeur.fins_orphelines


def arbre_lxml(texte: str) -> Noeud:
    """Arbre par lxml.html, converti sans récursion (texte de queue gardé à sa place)."""
    doc = lxml_html.document_fromstring(DECLARATION_XML.sub("", texte) or "<html></html>")
    racine = Noeud("#document", {})
    pile: list[tuple[Any, Noeud]] = [(doc, racine)]
    while pile:
        element, parent = pile.pop()
        if isinstance(element, str):
            ajouter_texte(parent, element)
            continue
        if not isinstance(element.tag, str):
            continue
        balise = element.tag.rsplit("}", 1)[-1].lower()
        noeud = Noeud(balise, {str(k).rsplit("}", 1)[-1].lower(): str(v) for k, v in element.attrib.items()}, [], parent)
        parent.enfants.append(noeud)
        ajouter_texte(noeud, element.text)
        for enfant in reversed(list(element)):
            if enfant.tail:
                pile.append((enfant.tail, noeud))
            pile.append((enfant, noeud))
    return racine


def ajouter_texte(noeud: Noeud, texte: str | None) -> None:
    """Ajoute du texte à la fin des enfants (fusionne avec une chaîne précédente)."""
    if not texte:
        return
    if noeud.enfants and isinstance(noeud.enfants[-1], str):
        noeud.enfants[-1] += texte
    else:
        noeud.enfants.append(texte)


def arbre_bs4(texte: str) -> Noeud:
    """Arbre par beautifulsoup4 (constructeur html.parser), converti sans récursion."""
    soupe = bs4.BeautifulSoup(texte, "html.parser")
    racine = Noeud("#document", {})
    pile: list[tuple[Any, Noeud]] = [(e, racine) for e in reversed(list(soupe.contents))]
    while pile:
        element, parent = pile.pop()
        if isinstance(element, bs4.element.Tag):
            attrs = {k.lower(): " ".join(v) if isinstance(v, list) else str(v) for k, v in element.attrs.items()}
            noeud = Noeud(element.name.lower(), attrs, [], parent)
            parent.enfants.append(noeud)
            pile.extend((e, noeud) for e in reversed(list(element.contents)))
        elif isinstance(element, bs4.element.NavigableString) and type(element) is bs4.element.NavigableString:
            ajouter_texte(parent, str(element))
    return racine


# --------------------------------------------------------------------------- parcours

def parcourir(racine: Noeud, sauter: frozenset[str] = frozenset()) -> Iterator[Noeud]:
    """Éléments en ordre du document, sans récursion ; ne descend pas dans `sauter`."""
    pile: list[Noeud] = [racine]
    while pile:
        noeud = pile.pop()
        yield noeud
        if noeud.balise in sauter and noeud is not racine:
            continue
        pile.extend(e for e in reversed(noeud.enfants) if isinstance(e, Noeud))


def texte_de(racine: Noeud, exclus: set[int] | None = None, blocs: bool = False) -> str:
    """Texte visible d'un sous-arbre ; `blocs` sépare les éléments de bloc par une ligne vide,
    garde les sauts de ligne de pre et de br."""
    morceaux: list[str] = []
    pile: list[Noeud | str] = [racine]
    while pile:
        element = pile.pop()
        if isinstance(element, str):
            morceaux.append(element)
            continue
        if element.balise in INVISIBLES or (exclus and id(element) in exclus):
            continue
        if blocs and element.balise == "pre":
            morceaux.append(SEPARATEUR + PREFORMATE + texte_preformate(element) + SEPARATEUR)
        elif blocs and element.balise == "br":
            morceaux.append(SAUT)
        elif blocs and element.balise in BLOCS:
            morceaux.append(SEPARATEUR)
            pile.append(SEPARATEUR)
            pile.extend(reversed(element.enfants))
        else:
            pile.extend(reversed(element.enfants))
    brut = "".join(morceaux)
    if not blocs:
        return BLANCS.sub(" ", brut.replace(SAUT, " ")).strip()
    sortie = []
    for segment in brut.split(SEPARATEUR):
        if segment.startswith(PREFORMATE):
            segment = segment[1:].strip("\n")
        else:
            segment = "\n".join(BLANCS.sub(" ", l).strip() for l in segment.split(SAUT)).strip()
        if segment.strip():
            sortie.append(segment)
    return "\n\n".join(sortie)


def texte_preformate(noeud: Noeud) -> str:
    """Texte d'un pre, blancs et sauts de ligne conservés."""
    morceaux: list[str] = []
    pile: list[Noeud | str] = [noeud]
    while pile:
        element = pile.pop()
        if isinstance(element, str):
            morceaux.append(element.replace(SEPARATEUR, " "))
        elif element.balise not in INVISIBLES:
            pile.extend(reversed(element.enfants))
    return "".join(morceaux)


def est_bruit(noeud: Noeud) -> bool:
    """Navigation, pied, formulaire, caché, ou classe/id de bruit (sauf section ou bloc titré)."""
    if noeud.balise in BRUIT or noeud.balise in INVISIBLES:
        return True
    a = noeud.attrs
    if "hidden" in a or a.get("aria-hidden", "").lower() == "true" or CACHE_STYLE.search(a.get("style", "")):
        return True
    if a.get("role", "").lower() in ROLES_BRUIT:
        return True
    if noeud.balise in ("main", "article", "body", "html", "section"):
        return False
    if any(isinstance(e, Noeud) and e.balise in TITRES for e in noeud.enfants):
        return False
    return bool(MOTIF_BRUIT.search(a.get("class", "")) or MOTIF_BRUIT.search(a.get("id", "")))


def noeuds_bruit(racine: Noeud, proteges: set[int]) -> set[int]:
    """Identifiants des sous-arbres retirés (sauf ceux qui contiennent le main protégé)."""
    retires: set[int] = set()
    for noeud in parcourir(racine):
        if id(noeud) not in proteges and est_bruit(noeud) and not ancetre_retire(noeud, retires):
            if noeud.balise == "header" and dans_article(noeud):
                continue
            retires.add(id(noeud))
    return retires


def ancetre_retire(noeud: Noeud, retires: set[int]) -> bool:
    courant = noeud.parent
    while courant is not None:
        if id(courant) in retires:
            return True
        courant = courant.parent
    return False


def dans_article(noeud: Noeud) -> bool:
    courant = noeud.parent
    while courant is not None:
        if courant.balise in ("article", "main"):
            return True
        courant = courant.parent
    return False


def ancetres(noeud: Noeud) -> set[int]:
    sortie: set[int] = set()
    courant: Noeud | None = noeud
    while courant is not None:
        sortie.add(id(courant))
        courant = courant.parent
    return sortie


# --------------------------------------------------------------------------- contenu principal

def choisir_principal(racine: Noeud) -> tuple[Noeud, str, set[int]]:
    """(nœud principal, méthode, nœuds retirés)."""
    mains = [n for n in parcourir(racine) if n.balise == "main" or n.attrs.get("role", "").lower() == "main"]
    proteges = set().union(*(ancetres(m) for m in mains)) if mains else set()
    retires = noeuds_bruit(racine, proteges)
    for m in mains:
        if len(texte_de(m, retires)) >= 50:
            return m, "main", retires
    articles = [n for n in parcourir(racine) if n.balise == "article" and id(n) not in retires
                and not ancetre_retire(n, retires) and len(texte_de(n, retires)) >= 200]
    if len(articles) == 1:
        return articles[0], "article", retires
    meilleur, scores = par_densite(racine, retires)
    return elargir(meilleur, scores, retires)


def par_densite(racine: Noeud, retires: set[int]) -> Noeud:
    """Bloc au plus fort score : paragraphes notés, score remonté au parent et grand-parent."""
    scores: dict[int, float] = {}
    noeuds: dict[int, Noeud] = {}
    for n in parcourir(racine):
        if n.balise not in PARAGRAPHES and not (n.balise == "div" and texte_direct(n) >= 25):
            continue
        if id(n) in retires or ancetre_retire(n, retires):
            continue
        texte = texte_de(n, retires)
        if len(texte) < 25:
            continue
        valeur = 1 + texte.count(",") + texte.count("，") + min(len(texte) / 100, 3)
        for niveau, cible in enumerate((n.parent, n.parent.parent if n.parent else None)):
            if cible is not None:
                noeuds[id(cible)] = cible
                scores[id(cible)] = scores.get(id(cible), 0) + valeur / (1 + niveau)
    if not scores:
        return next((n for n in parcourir(racine) if n.balise == "body"), racine), {}
    pondere = {k: v * (1 - densite_liens(noeuds[k], retires)) for k, v in scores.items()}
    return noeuds[max(pondere, key=pondere.get)], pondere


def elargir(meilleur: Noeud, scores: dict[int, float], retires: set[int]) -> tuple[Noeud, str, set[int]]:
    """Ajoute les voisins de bon score (sections sœurs, paragraphes, titres) : le parent devient
    le contenu principal et les voisins faibles sont retirés."""
    parent = meilleur.parent
    if parent is None or not scores:
        return meilleur, "densite", retires
    seuil = 0.2 * scores[id(meilleur)]
    volume = 0.2 * len(texte_de(meilleur, retires))
    gardes, ecartes = [], []
    for voisin in (e for e in parent.enfants if isinstance(e, Noeud)):
        longueur = len(texte_de(voisin, retires))
        bon = scores.get(id(voisin), 0) >= seuil or voisin.balise in TITRES or (
            (longueur >= volume or (voisin.balise in PARAGRAPHES | {"p"} and longueur > 80))
            and densite_liens(voisin, retires) < 0.33)
        (gardes if bon or voisin is meilleur else ecartes).append(voisin)
    if len(gardes) == 1:
        return meilleur, "densite", retires
    return parent, "densite+voisins", retires | {id(e) for e in ecartes}


def texte_direct(noeud: Noeud) -> int:
    """Longueur du texte porté directement par l'élément (hors enfants)."""
    return sum(len(e.strip()) for e in noeud.enfants if isinstance(e, str))


def densite_liens(noeud: Noeud, retires: set[int]) -> float:
    """Part du texte d'un bloc qui est du texte de lien."""
    total = len(texte_de(noeud, retires))
    if not total:
        return 1.0
    liens = sum(len(texte_de(a, retires)) for a in parcourir(noeud) if a.balise == "a")
    return min(liens / total, 1.0)


def chemin_css(noeud: Noeud) -> str:
    """Description courte d'un nœud : balise#id.classe."""
    morceau = noeud.balise
    if noeud.attrs.get("id"):
        morceau += f"#{noeud.attrs['id']}"
    if noeud.attrs.get("class"):
        morceau += "." + ".".join(noeud.attrs["class"].split()[:2])
    return morceau


# --------------------------------------------------------------------------- métadonnées, titres, liens

def metadonnees(racine: Noeud) -> dict[str, Any]:
    """title, langue, description, canonical, robots, OpenGraph, Twitter, base, charset."""
    meta: dict[str, Any] = {"titre": None, "titres_html": 0, "langue": None, "description": None,
                            "canonical": [], "robots": None, "opengraph": {}, "twitter": {},
                            "base": None, "charset_declare": None, "rafraichissement": None}
    for n in parcourir(racine):
        a = n.attrs
        if n.balise == "html" and meta["langue"] is None:
            meta["langue"] = a.get("lang") or a.get("xml:lang")
        elif n.balise == "title":
            meta["titres_html"] += 1
            meta["titre"] = meta["titre"] or texte_brut(n)
        elif n.balise == "base" and a.get("href") and not meta["base"]:
            meta["base"] = a["href"]
        elif n.balise == "link" and "canonical" in a.get("rel", "").lower().split():
            meta["canonical"].append(a.get("href", ""))
        elif n.balise == "meta":
            lire_meta(a, meta)
    return meta


def texte_brut(noeud: Noeud) -> str:
    """Texte d'un élément même s'il est invisible (title)."""
    return BLANCS.sub(" ", "".join(e for e in noeud.enfants if isinstance(e, str))).strip()


def lire_meta(a: dict[str, str], meta: dict[str, Any]) -> None:
    """Une balise meta."""
    nom = (a.get("name") or a.get("property") or "").strip().lower()
    contenu = a.get("content", "").strip()
    if a.get("charset"):
        meta["charset_declare"] = a["charset"]
    if a.get("http-equiv", "").lower() == "refresh":
        meta["rafraichissement"] = contenu
    if nom == "description" and meta["description"] is None:
        meta["description"] = contenu
    elif nom == "robots":
        meta["robots"] = contenu
    elif nom.startswith("og:"):
        meta["opengraph"].setdefault(nom[3:], contenu)
    elif nom.startswith("twitter:"):
        meta["twitter"].setdefault(nom[8:], contenu)


def titres_de(racine: Noeud, principal: Noeud) -> list[dict[str, Any]]:
    """Titres h1-h6 dans l'ordre, avec leur niveau et leur appartenance au contenu principal."""
    dedans = {id(n) for n in parcourir(principal)}
    return [{"niveau": int(n.balise[1]), "texte": texte_de(n), "id": n.attrs.get("id"),
             "dans_principal": id(n) in dedans}
            for n in parcourir(racine) if n.balise in TITRES]


def hote_site(meta: dict[str, Any], url_page: str | None) -> str | None:
    """Domaine de la page, s'il est connu."""
    for candidat in (url_page, *(meta["canonical"] or []), meta["opengraph"].get("url"), meta["base"]):
        if candidat and urlsplit(candidat).hostname:
            return urlsplit(candidat).hostname.lower()
    return None


def nom_accessible(a: Noeud) -> str:
    """Texte du lien, sinon aria-label, title, ou alt d'une image contenue."""
    texte = texte_de(a)
    if texte:
        return texte
    for cle in ("aria-label", "title"):
        if a.attrs.get(cle, "").strip():
            return a.attrs[cle].strip()
    alts = [n.attrs.get("alt", "").strip() for n in parcourir(a) if n.balise == "img"]
    return " ".join(x for x in alts if x)


def classer_lien(href: str, hote: str | None) -> str:
    """ancre, relatif, interne_absolu, externe, javascript, autre_schema, vide."""
    h = href.strip()
    if not h or h == "#":
        return "vide"
    if h.startswith("#"):
        return "ancre"
    schema = urlsplit(h).scheme.lower()
    if schema == "javascript":
        return "javascript"
    if schema and schema not in ("http", "https"):
        return "autre_schema"
    if schema or h.startswith("//"):
        cible = (urlsplit(h).hostname or "").lower()
        return "interne_absolu" if hote and (cible == hote or cible.endswith("." + hote)) else "externe"
    return "relatif"


def texte_trompeur(href: str, texte: str, genre: str, hote: str | None) -> str | None:
    """Gravité si le texte affiche un domaine autre que la cible (None sinon).

    Lien absolu : défaut si les domaines diffèrent. Lien relatif : défaut si le domaine du
    site est connu et diffère, avertissement s'il est inconnu et que le texte porte un schéma.
    Un texte « mot.mot » sans schéma ne compte que si son suffixe est un domaine courant.
    """
    montre = TEXTE_URL.match(texte.strip())
    if not montre or (not montre.group(1) and montre.group(3).lower() not in DOMAINES_COURANTS):
        return None
    affiche = montre.group(2).lower().removeprefix("www.")
    if genre == "relatif":
        if hote:
            return None if meme_domaine(hote, affiche) else "defaut"
        return "avertissement" if montre.group(1) else None
    if genre not in ("externe", "interne_absolu"):
        return None
    cible = (urlsplit(href if "//" in href else "//" + href).hostname or "").lower()
    return None if not cible or meme_domaine(cible, affiche) else "defaut"


def meme_domaine(a: str, b: str) -> bool:
    """Même domaine à « www. » près, ou l'un sous-domaine de l'autre."""
    a, b = a.removeprefix("www."), b.removeprefix("www.")
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def liens_de(racine: Noeud, principal: Noeud, meta: dict[str, Any], url_page: str | None,
             ids: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Liens a[href] classés, et les défauts qu'ils portent."""
    hote = hote_site(meta, url_page)
    base = url_page or meta["base"] or (meta["canonical"][0] if meta["canonical"] else None)
    dedans = {id(n) for n in parcourir(principal)}
    liens, constats = [], []
    for a in parcourir(racine):
        if a.balise != "a" or "href" not in a.attrs:
            continue
        href = a.attrs["href"].strip()
        texte = nom_accessible(a)
        genre = classer_lien(href, hote)
        lien = {"texte": texte[:120], "cible": href, "genre": genre, "dans_principal": id(a) in dedans}
        if base and genre in ("relatif", "interne_absolu", "externe"):
            lien["cible_resolue"] = urljoin(base, href)
        liens.append(lien)
        constats += defauts_de_lien(href, texte, genre, ids, hote)
    return liens, constats


def defauts_de_lien(href: str, texte: str, genre: str, ids: set[str], hote: str | None) -> list[dict[str, str]]:
    """Ancre absente, texte trompeur, lien sans nom, javascript."""
    sortie = []
    if genre == "ancre" and href[1:] not in ids and href[1:].lower() != "top":
        sortie.append({"gravite": "defaut", "type": "ancre_absente", "detail": f"{href} : aucun id ni name"})
    trompeur = texte_trompeur(href, texte, genre, hote)
    if trompeur:
        sortie.append({"gravite": trompeur, "type": "texte_trompeur",
                       "detail": f"le texte affiche « {texte[:60]} » mais mène à « {href[:80]} »"})
    if not texte:
        sortie.append({"gravite": "avertissement", "type": "lien_sans_nom", "detail": href[:80]})
    if genre == "javascript":
        sortie.append({"gravite": "avertissement", "type": "lien_javascript", "detail": href[:80]})
    return sortie


# --------------------------------------------------------------------------- contrôles de page

def controles_page(racine: Noeud, meta: dict[str, Any], titres: list[dict[str, Any]]) -> tuple[list[dict[str, str]], set[str]]:
    """Défauts et avertissements de la page entière ; rend aussi l'ensemble des id/name."""
    constats: list[dict[str, str]] = []
    vus: Counter[str] = Counter()
    noms: set[str] = set()
    images_sans_alt = 0
    for n in parcourir(racine):
        if n.attrs.get("id"):
            vus[n.attrs["id"]] += 1
        if n.balise == "a" and n.attrs.get("name"):
            noms.add(n.attrs["name"])
        if n.balise == "img" and "alt" not in n.attrs:
            images_sans_alt += 1
    for ident, nombre in vus.items():
        if nombre > 1:
            constats.append({"gravite": "defaut", "type": "id_duplique", "detail": f"id « {ident} » × {nombre}"})
    if not meta["titre"]:
        constats.append({"gravite": "defaut", "type": "titre_absent", "detail": "aucun <title> non vide"})
    if len(set(meta["canonical"])) > 1:
        constats.append({"gravite": "defaut", "type": "canonical_contradictoires", "detail": " ; ".join(meta["canonical"])})
    constats += avertissements_page(meta, titres, images_sans_alt)
    return constats, set(vus) | noms


def avertissements_page(meta: dict[str, Any], titres: list[dict[str, Any]], sans_alt: int) -> list[dict[str, str]]:
    """Langue, description, structure des titres, images sans alt."""
    sortie = []
    if meta["titres_html"] > 1:
        sortie.append({"gravite": "avertissement", "type": "plusieurs_title", "detail": f"{meta['titres_html']} balises title"})
    if not meta["langue"]:
        sortie.append({"gravite": "avertissement", "type": "langue_absente", "detail": "html sans attribut lang"})
    if not meta["description"]:
        sortie.append({"gravite": "avertissement", "type": "description_absente", "detail": "pas de meta description"})
    h1 = sum(t["niveau"] == 1 for t in titres)
    if h1 != 1:
        sortie.append({"gravite": "avertissement", "type": "h1", "detail": f"{h1} titre(s) h1"})
    for avant, apres in zip(titres, titres[1:]):
        if apres["niveau"] > avant["niveau"] + 1:
            sortie.append({"gravite": "avertissement", "type": "saut_de_niveau",
                           "detail": f"h{avant['niveau']} puis h{apres['niveau']} « {apres['texte'][:50]} »"})
    sortie += [{"gravite": "avertissement", "type": "titre_vide", "detail": f"h{t['niveau']} vide"}
               for t in titres if not t["texte"]]
    if sans_alt:
        sortie.append({"gravite": "avertissement", "type": "image_sans_alt", "detail": f"{sans_alt} image(s) sans attribut alt"})
    return sortie


# --------------------------------------------------------------------------- lecture

def decoder(octets: bytes) -> tuple[str, str, bool]:
    """(texte, encodage, encodage supposé) ; lève EntreeInvalide si binaire."""
    for bom, nom in ((b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if octets.startswith(bom):
            return octets.decode(nom, errors="replace"), nom, False
    if b"\x00" in octets[:65536]:
        raise EntreeInvalide("contenu binaire (octets nuls)")
    declare = CHARSET.search(octets[:4096])
    if declare:
        try:
            nom = codecs.lookup(declare.group(1).decode("ascii")).name
            return octets.decode(nom, errors="replace"), nom, False
        except (LookupError, UnicodeError):
            pass
    try:
        return octets.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError:
        return octets.decode("cp1252", errors="replace"), "cp1252", True


def lire_fichier(chemin: Path, max_octets: int) -> tuple[str, str, bool]:
    """Lit un fichier borné."""
    try:
        with chemin.open("rb") as flux:
            octets = flux.read(max_octets + 1)
    except OSError as exc:
        raise EntreeInvalide(f"illisible : {exc.strerror or exc}") from exc
    if len(octets) > max_octets:
        raise EntreeInvalide(f"plus de {max_octets} octets (--max-octets)")
    return decoder(octets)


def construire_arbre(texte: str, moteur: str) -> tuple[Noeud, int]:
    """Arbre selon le moteur ; lève EntreeInvalide si ce n'est pas du html."""
    if not re.search(r"<[A-Za-z!/]", texte):
        raise EntreeInvalide("aucune balise : pas du html")
    if moteur == "lxml":
        return arbre_lxml(texte), 0
    if moteur == "bs4":
        return arbre_bs4(texte), 0
    return arbre_stdlib(texte)


def comparer_trafilatura(texte_html: str, principal: str) -> dict[str, Any] | None:
    """Recouvrement des mots entre le texte principal de l'outil et celui de trafilatura."""
    if trafilatura is None:
        return None
    nom = f"trafilatura {version_de('trafilatura')}"
    try:
        autre = trafilatura.extract(texte_html, include_comments=False, include_tables=True) or ""
    except (ValueError, TypeError, AttributeError) as exc:
        return {"bibliotheque": nom, "erreur": f"{type(exc).__name__} : {str(exc)[:200]}"}
    a = Counter(re.findall(r"\w+", principal.lower()))
    b = Counter(re.findall(r"\w+", autre.lower()))
    commun = sum((a & b).values())
    return {"bibliotheque": nom, "mots_trafilatura": sum(b.values()), "mots_outil": sum(a.values()),
            "part_des_mots_trafilatura_retrouvee": round(commun / sum(b.values()), 3) if b else None,
            "part_des_mots_outil_chez_trafilatura": round(commun / sum(a.values()), 3) if a else None}


def version_de(nom: str) -> str:
    """Version installée d'une distribution, ou '?'."""
    from importlib import metadata
    try:
        return metadata.version(nom)
    except metadata.PackageNotFoundError:
        return "?"


def analyser(nom: str, texte: str, encodage: str, suppose: bool, reglages: dict[str, Any]) -> dict[str, Any]:
    """Analyse complète d'une page."""
    racine, orphelines = construire_arbre(texte, reglages["moteur"])
    meta = metadonnees(racine)
    principal, methode, retires = choisir_principal(racine)
    titres = titres_de(racine, principal)
    constats, ids = controles_page(racine, meta, titres)
    liens, constats_liens = liens_de(racine, principal, meta, reglages["url_page"], ids)
    constats += constats_liens
    if suppose:
        constats.append({"gravite": "avertissement", "type": "encodage", "detail": "ni utf-8 ni charset déclaré : lu en cp1252"})
    texte_principal = texte_de(principal, retires, blocs=True)
    mots_visibles = len(re.findall(r"\w+", texte_de(next((n for n in parcourir(racine) if n.balise == "body"), racine))))
    mots_principal = len(re.findall(r"\w+", texte_principal))
    return {
        "chemin": nom, "encodage": encodage, "moteur": reglages["moteur"],
        "verdict": "defauts" if any(c["gravite"] == "defaut" for c in constats) else "conforme",
        "metadonnees": meta, "titres": titres[:PLAFOND_LISTE],
        "liens": resume_liens(liens, reglages["max_liens"]),
        "contenu_principal": {
            "methode": methode, "element": chemin_css(principal),
            "caracteres": len(texte_principal), "mots": mots_principal,
            "part_des_mots_visibles": round(mots_principal / mots_visibles, 3) if mots_visibles else None,
            "texte": texte_principal[:reglages["max_caracteres"]],
            "tronque": len(texte_principal) > reglages["max_caracteres"],
        },
        "balises_fermantes_orphelines": orphelines,
        "defauts": [c for c in constats if c["gravite"] == "defaut"][:PLAFOND_LISTE],
        "avertissements": [c for c in constats if c["gravite"] != "defaut"][:PLAFOND_LISTE],
        "comparaison": comparer_trafilatura(texte, texte_principal) if reglages["comparer"] else None,
    }


def resume_liens(liens: list[dict[str, Any]], plafond: int) -> dict[str, Any]:
    """Comptes par genre, domaines externes, liste plafonnée."""
    externes = Counter((urlsplit(l["cible"] if "//" in l["cible"] else "//" + l["cible"]).hostname or "?")
                       for l in liens if l["genre"] == "externe")
    return {"total": len(liens), "par_genre": dict(Counter(l["genre"] for l in liens)),
            "dans_principal": sum(l["dans_principal"] for l in liens),
            "domaines_externes": dict(externes.most_common(20)),
            "liste": liens[:plafond], "liste_tronquee": len(liens) > plafond}


# --------------------------------------------------------------------------- interface

def lire_contrat() -> dict[str, str]:
    """Le contrat de mesure, relu dans la docstring."""
    sections: dict[str, list[str]] = {}
    courant = None
    for ligne in (__doc__ or "").splitlines():
        if ligne.strip() in INTITULES and not ligne.startswith(" "):
            courant = ligne.strip()
            sections[courant] = []
        elif courant:
            sections[courant].append(ligne.strip())
    return {cle: " ".join(m for m in morceaux if m) for cle, morceaux in sections.items()}


def afficher_humain(rapport: dict[str, Any]) -> None:
    for r in rapport["resultats"]:
        m, c = r["metadonnees"], r["contenu_principal"]
        print(f"{r['chemin']} : {r['verdict'].upper()} ({r['moteur']}, {r['encodage']})")
        print(f"  titre : {m['titre']!r} ; langue : {m['langue']} ; canonical : {', '.join(m['canonical']) or '-'}")
        print(f"  description : {m['description']!r}")
        if m["opengraph"]:
            print(f"  OpenGraph : {json.dumps(m['opengraph'], ensure_ascii=False)}")
        for t in r["titres"][:40]:
            print(f"  {'  ' * (t['niveau'] - 1)}h{t['niveau']} {t['texte'][:90]}")
        print(f"  liens : {json.dumps(r['liens']['par_genre'], ensure_ascii=False)}")
        print(f"  contenu principal ({c['methode']} {c['element']}, {c['mots']} mots) :")
        for ligne in c["texte"][:1500].split("\n"):
            print(f"    {ligne}")
        for d in r["defauts"] + r["avertissements"][:20]:
            print(f"  {d['gravite']} {d['type']} : {d['detail']}")
        if r["comparaison"]:
            print(f"  comparaison : {json.dumps(r['comparaison'], ensure_ascii=False)}")
    print(f"{rapport['denominateur']} page(s), {rapport['conformes']} sans défaut")


def afficher_json(rapport: dict[str, Any]) -> None:
    print(json.dumps(rapport, ensure_ascii=False, indent=2))


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        description="Lit une page html enregistrée (jamais le réseau) : titre, métadonnées, titres, "
                    "liens classés et contenu principal débarrassé des menus et pieds de page.",
        epilog="Exemples : python lire_html.py page.html --json\n"
               "          python lire_html.py site_exporte/ --url-page https://exemple.org/\n"
               "Codes : 0 sans défaut, 1 défaut, 2 entrée invalide, 3 aucune page à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parseur.add_argument("chemins", nargs="*", help="fichiers .html/.htm/.xhtml ou dossiers")
    parseur.add_argument("--texte", help="html donné en ligne")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="dossier de base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--moteur", choices=("auto", "stdlib", "lxml", "bs4"), default="auto",
                         help="analyseur html (auto : lxml, sinon bs4, sinon stdlib)")
    parseur.add_argument("--url-page", help="adresse de la page, pour classer les liens absolus (rien n'est visité)")
    parseur.add_argument("--sans-comparaison", action="store_true", help="n'appelle pas trafilatura")
    parseur.add_argument("--max-caracteres", type=int, default=20000, help="texte principal rendu au plus")
    parseur.add_argument("--max-liens", type=int, default=200, help="liens détaillés au plus par page")
    parseur.add_argument("--max-octets", type=int, default=32 << 20, help="taille maximale d'une page")
    parseur.add_argument("--max-fichiers", type=int, default=2000, help="pages lues au plus")
    return parseur


def base_relative(racine: Path | None) -> Path:
    """Base des chemins relatifs : --racine, sinon le dossier courant (sinon celui de l'outil)."""
    if racine is not None:
        return racine
    try:
        return Path.cwd()
    except OSError:
        return RACINE


def choisir_moteur(demande: str) -> str:
    """lxml > bs4 > stdlib ; une ligne sur stderr en mode dégradé."""
    disponibles = {"lxml": lxml_html is not None, "bs4": bs4 is not None, "stdlib": True}
    if demande != "auto":
        if not disponibles[demande]:
            raise EntreeInvalide(f"--moteur {demande} demandé mais la bibliothèque est absente")
        return demande
    for moteur in ("lxml", "bs4"):
        if disponibles[moteur]:
            return moteur
    print("mode dégradé — lxml et beautifulsoup4 absents : analyse par html.parser (stdlib) ; "
          + ("trafilatura présent pour comparer" if trafilatura else "trafilatura absent : pas de comparaison"),
          file=sys.stderr)
    return "stdlib"


def collecter(args: argparse.Namespace) -> list[tuple[str, Path | None, str | None]]:
    """Sources à lire ; lève EntreeInvalide pour un chemin absent."""
    base = base_relative(args.racine)
    sources: list[tuple[str, Path | None, str | None]] = []
    for brut in args.chemins:
        chemin = Path(brut) if Path(brut).is_absolute() else base / brut
        if chemin.is_dir():
            for racine, dossiers, fichiers in os.walk(chemin):
                dossiers[:] = sorted(d for d in dossiers if d not in DOSSIERS_IGNORES and not d.startswith("."))
                sources += [(str(Path(racine) / f), Path(racine) / f, None) for f in sorted(fichiers)
                            if Path(f).suffix.lower() in EXTENSIONS]
        elif chemin.is_file():
            sources.append((str(chemin), chemin, None))
        else:
            raise EntreeInvalide(f"chemin introuvable : {brut}")
    if args.texte is not None:
        sources.append(("<texte>", None, args.texte))
    return sources[:args.max_fichiers]


def main() -> int:
    args = construire_parseur().parse_args()
    try:
        if not args.chemins and args.texte is None:
            raise EntreeInvalide("donner un fichier, un dossier ou --texte")
        if min(args.max_caracteres, args.max_liens, args.max_octets, args.max_fichiers) < 1:
            raise EntreeInvalide("plafonds positifs attendus")
        reglages = {"moteur": choisir_moteur(args.moteur), "url_page": args.url_page,
                    "comparer": not args.sans_comparaison, "max_liens": args.max_liens,
                    "max_caracteres": args.max_caracteres}
        sources = collecter(args)
    except EntreeInvalide as exc:
        print(f"entrée invalide : {exc}", file=sys.stderr)
        return 2
    resultats, illisibles = [], []
    for nom, chemin, texte in sources:
        try:
            texte, encodage, suppose = lire_fichier(chemin, args.max_octets) if chemin else (texte or "", "utf-8", False)
            resultats.append(analyser(nom, texte, encodage, suppose, reglages))
        except EntreeInvalide as exc:
            illisibles.append({"chemin": nom, "raison": str(exc)})
    noms = [r["chemin"] for r in resultats]
    rapport = {"outil": NOM_OUTIL, "moteur": reglages["moteur"],
               "comparaison_avec": f"trafilatura {version_de('trafilatura')}" if trafilatura and reglages["comparer"] else None,
               "denominateur": len(resultats), "examines": noms[:PLAFOND_LISTE],
               "examines_tronques": len(noms) > PLAFOND_LISTE,
               "conformes": sum(r["verdict"] == "conforme" for r in resultats),
               "resultats": resultats, "illisibles": illisibles, "contrat": lire_contrat()}
    if args.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    for i in illisibles:
        print(f"entrée invalide : {i['chemin']} : {i['raison']}", file=sys.stderr)
    if illisibles:
        return 2
    if not resultats:
        print("dénominateur nul : aucune page html trouvée, rien à examiner", file=sys.stderr)
        return 3
    return 0 if rapport["conformes"] == len(resultats) else 1


if __name__ == "__main__":
    raise SystemExit(main())
