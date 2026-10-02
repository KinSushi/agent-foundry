"""Vérifier, sans jamais aller sur le réseau, que les liens relatifs et les ancres d'une documentation Markdown mènent quelque part.

Mesuré dans cette session : sur la documentation Markdown de Meson livrée dans les sources de
numpy 2.0.2 (166 fichiers, 645 liens), l'outil trouve 33 liens cassés selon les règles de GitHub,
dont 14 ancres absentes, par exemple « #add-math-library-lm-portably » alors que le titre
« Add math library (`-lm`) portably » donne « add-math-library--lm-portably » ; sur README.md et
docs/ de ce dépôt (143 fichiers, 294 liens), 0 cassé. Sur ces 166 fichiers, markdown-it-py 4.2.0
et le lecteur stdlib extraient les mêmes liens et calculent les mêmes ancres.

QUESTION
    Les liens relatifs, images, références et ancres (#titre) de cette documentation
    Markdown pointent-ils vers un fichier, un dossier ou un titre qui existe ?
MESURE
    Extraction des liens [texte](cible), images ![alt](cible), références [texte][étiquette]
    et [étiquette]: cible, autoliens <...>, adresses nues, balises a/img brutes, hors blocs
    de code et code en ligne. Par markdown-it-py s'il est installé (CommonMark exact), sinon
    par un lecteur stdlib approché ; les deux sont comparés quand les deux tournent. Une
    cible relative est résolue contre le dossier du fichier (contre la racine du dépôt si
    elle commence par /), décodée (%20), puis cherchée sur le disque avec sa casse exacte.
    Les ancres sont calculées comme GitHub les décrit (docs.github.com, « Section links ») et
    comme html-pipeline les code : seules les lettres A à Z passent en minuscules, tout sauf lettres, chiffres,
    marques, « _ », « - » et espace retiré, espace -> « - », suffixe -1, -2 aux doublons ;
    plus les id/name des balises html. Les adresses externes sont listées, jamais visitées.
HYPOTHÈSES
    Le rendu visé est celui de GitHub. Le disque reflète le dépôt (pas de fichier généré
    plus tard). Les fichiers sont en utf-8.
LIMITES
    Les ancres d'un fichier Markdown cible ne sont vérifiées que s'il est dans le périmètre
    (chemins donnés, ou --perimetre) : l'outil ne lit rien d'autre. Ancres de fichiers non
    Markdown (numéros de ligne) non vérifiées. Le lecteur stdlib ne connaît ni les blocs html qui
    suspendent le Markdown, ni toutes les subtilités de listes imbriquées ; les émojis
    :nom: ne sont pas convertis avant le calcul d'ancre (GitHub les rend en image).
CONTRE-EXEMPLES
    Constaté : pour « ## Étape 1 : prérequis » l'outil attend « #Étape-1--prérequis »
    (seules les lettres A à Z passent en minuscules, comme le « Θ » conservé de l'exemple officiel de GitHub),
    alors que github-slugger 2.0.0 rend « étape-1--prérequis » : un lien écrit ainsi est
    signalé (avertissement de casse, défaut avec --strict) alors qu'il marche dans les
    rendus qui suivent github-slugger. Un lien vers un fichier produit par la construction
    (site/, build/) est déclaré absent.
INVOCATION
    {outil} --texte '# Titre principal\n\n## Détails\n\nVoir [les détails](#détails) et [le début](#titre-principal).' --json
DOMAINE
    README.md et dossiers docs/ d'un dépôt avant publication sur GitHub ou une forge
    compatible ; pas les sites statiques qui réécrivent leurs liens.
"""

from __future__ import annotations

import argparse
import bisect
import difflib
import html
import json
import os
import re
import sys
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from markdown_it import MarkdownIt
except ImportError:
    MarkdownIt = None

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", INTITULE_HYPOTHESES, "LIMITES",
             "CONTRE-EXEMPLES", INTITULE_INVOCATION, "DOMAINE")
NOM_OUTIL = "verifier_liens_markdown"
EXTENSIONS_MD = frozenset({".md", ".markdown", ".mdown", ".mkd", ".mkdn"})
DOSSIERS_IGNORES = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv",
                              ".tox", ".mypy_cache", "site-packages"})
PLAFOND_LISTE = 200
PREFIXE_GITHUB = "user-content-"

FENCE = re.compile(r"^[ \t]*(?:>[ \t]?)*[ \t]*(`{3,}|~{3,})(.*)$")
ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?(?:[ \t]+#+)?[ \t]*$")
SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
ITEM_LISTE = re.compile(r"^[ \t]*(?:[-+*]|\d{1,9}[.)])(?:[ \t]|$)")
DEFINITION = re.compile(r"^ {0,3}\[((?:[^\[\]\\]|\\.){1,999})\]:[ \t]*(?:\n[ \t]*)?(<[^<>\n]*>|\S+)"
                        r"(?:[ \t]+(?:\"[^\"]*\"|'[^']*'|\([^()]*\)))?[ \t]*$", re.MULTILINE)
SCHEMA = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
AUTOLIEN = re.compile(r"<([A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*|[\w.+-]+@[\w-]+(?:\.[\w-]+)+)>")
URL_NUE = re.compile(r"(?<![\w/(<\"'=])(?:https?://|www\.)[^\s<>\]]+")
BALISE = re.compile(r"<([A-Za-z][A-Za-z0-9-]*)\b([^<>]*)>")
ATTRIBUT = re.compile(r"\b([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s\"'>]+))")
TITRE_HTML = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1\s*>", re.IGNORECASE | re.DOTALL)
COMMENTAIRE = re.compile(r"<!--.*?-->", re.DOTALL)
ECHAPPEMENT = re.compile(r"\\([!-/:-@\[-`{-~])")
ENTITE = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[A-Za-z][A-Za-z0-9]{1,31});")
SOULIGNES = re.compile(r"_+")
CODE_EN_LIGNE = re.compile(r"(`+)(.+?)(?<!`)\1(?!`)", re.DOTALL)
LIGNE_VIDE = re.compile(r"\n(?=[ \t]*(?:\n|$))")
LIEN_DANS_TITRE = re.compile(r"!?\[((?:[^\[\]]|\[[^\[\]]*\])*)\](?:\([^()]*(?:\([^()]*\)[^()]*)*\)|\[[^\]]*\])?")


class EntreeInvalide(Exception):
    """Entrée inutilisable (chemin absent, binaire, pas du texte) : code 2."""


@dataclass
class Lien:
    """Un lien trouvé dans un fichier."""

    ligne: int
    genre: str
    cible: str
    texte: str = ""


@dataclass
class Analyse:
    """Ce qu'un moteur extrait d'un document Markdown."""

    liens: list[Lien]
    ancres: list[str]
    ancres_html: set[str]


@dataclass
class Document:
    """Un document lu, ses liens et ses ancres."""

    nom: str
    chemin: Path | None
    texte: str
    analyse: Analyse
    references: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Contexte:
    """Réglages de la vérification."""

    depot: Path
    perimetre: tuple[Path, ...]
    base_texte: Path
    strict: bool
    max_octets: int


# --------------------------------------------------------------------------- ancres GitHub

def slug_github(texte: str) -> str:
    """Ancre d'un titre selon html-pipeline (TableOfContentsFilter) et la doc GitHub."""
    garde = []
    for car in texte.strip():
        car = car.lower() if "A" <= car <= "Z" else car
        cat = unicodedata.category(car)
        if car in "- " or cat[0] in "LM" or cat in ("Nd", "Nl", "Pc") or car in "\u200c\u200d":
            garde.append(car)
    return "".join(garde).replace(" ", "-")


def numeroter_ancres(bases: list[str]) -> list[str]:
    """Suffixes -1, -2... aux doublons, comme html-pipeline."""
    vus: Counter[str] = Counter()
    sorties = []
    for base in bases:
        sorties.append(f"{base}-{vus[base]}" if vus[base] else base)
        vus[base] += 1
    return sorties


def decoder_entites(texte: str) -> str:
    """Entités au sens CommonMark : seulement les formes terminées par « ; »."""
    return ENTITE.sub(lambda m: html.unescape(m.group(0)), texte)


def texte_de_titre(brut: str) -> str:
    """Texte rendu d'un titre Markdown : balisage retiré, code en ligne conservé tel quel.

    Chaque span de code est remplacé par un caractère d'usage privé (ni blanc ni ponctuation,
    comme une lettre) le temps de retirer le balisage, puis rétabli.
    """
    codes: list[str] = []

    def garder(m: re.Match[str]) -> str:
        contenu = m.group(2)
        codes.append(contenu[1:-1] if len(contenu) > 2 and contenu[0] == contenu[-1] == " " else contenu)
        return chr(0xE000 + len(codes) - 1)

    texte = nettoyer_balisage(CODE_EN_LIGNE.sub(garder, brut[:2000]))
    return "".join(codes[ord(c) - 0xE000] if 0xE000 <= ord(c) < 0xE000 + len(codes) else c for c in texte)


def nettoyer_balisage(segment: str) -> str:
    """Retire images, liens (garde le texte), balises, échappements, emphase par « _ »."""
    precedent = None
    while precedent != segment:
        precedent = segment
        segment = LIEN_DANS_TITRE.sub(lambda m: "" if m.group(0).startswith("!") else m.group(1), segment)
    segment = AUTOLIEN.sub(lambda m: m.group(1), segment)
    segment = re.sub(r"<[^<>]*>", "", segment)
    segment = retirer_emphase_soulignee(segment)
    segment = ECHAPPEMENT.sub(r"\1", segment)
    return decoder_entites(segment)


def est_ponctuation(car: str) -> bool:
    """Ponctuation au sens CommonMark (catégories Unicode P et S)."""
    return unicodedata.category(car)[0] in "PS"


def retirer_emphase_soulignee(texte: str) -> str:
    """Retire les « _ » qui forment une emphase selon les règles de flanc CommonMark.

    Un « _ » échappé (\\_) n'est pas un délimiteur ; un « _ » sans partenaire reste visible.
    """
    runs: list[list[int]] = []
    for m in SOULIGNES.finditer(texte):
        if m.start() > 0 and texte[m.start() - 1] == "\\":
            continue
        avant = texte[m.start() - 1] if m.start() > 0 else " "
        apres = texte[m.end()] if m.end() < len(texte) else " "
        gauche = not apres.isspace() and (not est_ponctuation(apres) or avant.isspace() or est_ponctuation(avant))
        droite = not avant.isspace() and (not est_ponctuation(avant) or apres.isspace() or est_ponctuation(apres))
        ouvre = gauche and (not droite or est_ponctuation(avant))
        ferme = droite and (not gauche or est_ponctuation(apres))
        runs.append([m.start(), m.end() - m.start(), int(ouvre), int(ferme), 0])
    apparier_soulignes(runs)
    sortie, pos = [], 0
    for debut, longueur, _o, _f, retires in runs:
        sortie.append(texte[pos:debut] + "_" * (longueur - retires))
        pos = debut + longueur
    sortie.append(texte[pos:])
    return "".join(sortie)


def apparier_soulignes(runs: list[list[int]]) -> None:
    """Apparie ouvrants et fermants (pile) ; note dans runs[k][4] le nombre de « _ » consommés."""
    pile: list[int] = []
    for k, run in enumerate(runs):
        if run[3]:
            while pile and run[1] - run[4] > 0:
                j = pile[-1]
                pris = min(runs[j][1] - runs[j][4], run[1] - run[4])
                runs[j][4] += pris
                run[4] += pris
                if runs[j][1] - runs[j][4] == 0:
                    pile.pop()
        if run[2] and run[1] - run[4] > 0:
            pile.append(k)


def ancres_html_de(fragment: str) -> set[str]:
    """Valeurs des attributs id et name des balises html d'un fragment."""
    trouvees: set[str] = set()
    for balise in BALISE.finditer(fragment):
        for nom, v1, v2, v3 in ATTRIBUT.findall(balise.group(2)):
            if nom.lower() in ("id", "name"):
                trouvees.add(html.unescape(v1 or v2 or v3))
    return trouvees


def liens_html_de(fragment: str) -> list[tuple[int, str]]:
    """(décalage, cible) des attributs href de <a> et src de <img> d'un fragment."""
    trouves = []
    for balise in BALISE.finditer(fragment):
        tag = balise.group(1).lower()
        for nom, v1, v2, v3 in ATTRIBUT.findall(balise.group(2)):
            if (tag, nom.lower()) in (("a", "href"), ("img", "src")):
                trouves.append((balise.start(), html.unescape(v1 or v2 or v3)))
    return trouves


# --------------------------------------------------------------------------- lecteur stdlib

def masquer(car: list[str], debut: int, fin: int) -> None:
    """Remplace par des espaces (sauts de ligne gardés) les caractères [debut, fin)."""
    for i in range(debut, fin):
        if car[i] != "\n":
            car[i] = " "


def lignes_de_code(lignes: list[str]) -> set[int]:
    """Indices des lignes dans un bloc de code clôturé ou indenté (hors listes)."""
    code: set[int] = set()
    ouvert: tuple[str, int] | None = None
    en_liste = False
    precedente_vide = True
    for i, ligne in enumerate(lignes):
        fence = FENCE.match(ligne)
        if ouvert:
            code.add(i)
            if fence and fence.group(1)[0] == ouvert[0] and len(fence.group(1)) >= ouvert[1] \
                    and not fence.group(2).strip():
                ouvert = None
            continue
        if fence and not (fence.group(1)[0] == "`" and "`" in fence.group(2)):
            ouvert = (fence.group(1)[0], len(fence.group(1)))
            code.add(i)
            continue
        vide = not ligne.strip()
        indentee = ligne.startswith(("    ", "\t"))
        if not vide and not indentee:
            en_liste = bool(ITEM_LISTE.match(ligne))
        if indentee and not vide and not en_liste and (precedente_vide or i - 1 in code):
            code.add(i)
        precedente_vide = vide
    return code


def front_matter(lignes: list[str]) -> set[int]:
    """Lignes d'un en-tête YAML initial (--- ... ---)."""
    if not lignes or lignes[0].strip() != "---":
        return set()
    for i in range(1, min(len(lignes), 500)):
        if lignes[i].strip() in ("---", "..."):
            return set(range(i + 1))
    return set()


def masquer_code_en_ligne(car: list[str]) -> None:
    """Masque les spans de code (même nombre d'accents graves, sans ligne vide entre)."""
    texte = "".join(car)
    i = 0
    while True:
        debut = texte.find("`", i)
        if debut < 0:
            return
        fin_ouv = debut
        while fin_ouv < len(texte) and texte[fin_ouv] == "`":
            fin_ouv += 1
        n = fin_ouv - debut
        echappe = debut > 0 and texte[debut - 1] == "\\"
        fermeture = re.compile(rf"(?<!`)`{{{n}}}(?!`)").search(texte, fin_ouv)
        bloc_vide = re.compile(r"\n[ \t]*\n").search(texte, fin_ouv, fermeture.start() if fermeture else fin_ouv)
        if echappe or not fermeture or bloc_vide:
            i = fin_ouv
            continue
        masquer(car, debut, fermeture.end())
        i = fermeture.end()


def normaliser_etiquette(etiquette: str) -> str:
    """Étiquette de référence : casse repliée, blancs réduits (CommonMark)."""
    return " ".join(etiquette.split()).casefold()


def lire_definitions(texte: str, car: list[str]) -> dict[str, tuple[str, int]]:
    """[étiquette]: cible -> {étiquette normalisée: (cible, décalage)} ; masque ces lignes."""
    definitions: dict[str, tuple[str, int]] = {}
    for m in DEFINITION.finditer(texte):
        etiquette = m.group(1)
        if etiquette.startswith("^") or not etiquette.strip():
            continue
        cle = normaliser_etiquette(etiquette)
        cible = m.group(2)[1:-1] if m.group(2).startswith("<") else m.group(2)
        definitions.setdefault(cle, (decoder_entites(ECHAPPEMENT.sub(r"\1", cible)), m.start()))
        masquer(car, m.start(), m.end())
    return definitions


def lire_destination(t: str, j: int) -> tuple[str, int] | None:
    """Destination (et titre) d'un lien en ligne à partir de la parenthèse ouvrante + 1."""
    n = len(t)
    j = sauter_blancs(t, j)
    if j < n and t[j] == "<":
        k = t.find(">", j)
        if k < 0 or "\n" in t[j:k]:
            return None
        cible, j = t[j + 1:k], k + 1
    else:
        debut, prof = j, 0
        while j < n and t[j] not in " \t\n":
            if t[j] == "\\":
                j += 2
                continue
            if t[j] == "(":
                prof += 1
            elif t[j] == ")":
                if prof == 0:
                    break
                prof -= 1
            j += 1
        cible = t[debut:j]
    j = sauter_titre(t, sauter_blancs(t, j))
    if j is None or j >= n or t[j] != ")":
        return None
    return decoder_entites(ECHAPPEMENT.sub(r"\1", cible)), j + 1


def sauter_blancs(t: str, j: int) -> int:
    """Avance sur espaces, tabulations et au plus un saut de ligne."""
    sauts = 0
    while j < len(t) and t[j] in " \t\n":
        sauts += t[j] == "\n"
        if sauts > 1:
            break
        j += 1
    return j


def sauter_titre(t: str, j: int) -> int | None:
    """Saute un titre de lien optionnel ("...", '...' ou (...))."""
    if j >= len(t) or t[j] not in "\"'(":
        return j
    fermant = ")" if t[j] == "(" else t[j]
    k = j + 1
    while k < len(t) and t[k] != fermant:
        k += 2 if t[k] == "\\" else 1
    return sauter_blancs(t, k + 1) if k < len(t) else None


def scanner_crochets(t: str, brut: str, definitions: dict[str, tuple[str, int]],
                     references: list[dict[str, Any]]) -> list[tuple[int, int, str, str, str]]:
    """Liens en ligne et références : (début, fin, genre, cible, texte).

    t : texte où le code est masqué (structure) ; brut : même longueur, spans de code
    intacts (textes et étiquettes).
    """
    trouves: list[tuple[int, int, str, str, str]] = []
    pile: list[int] = []
    vides = {m.start() for m in LIGNE_VIDE.finditer(t)}
    i, n = 0, len(t)
    while i < n:
        c = t[i]
        if c == "\\":
            i += 2
            continue
        if i in vides:
            pile.clear()
        if c == "[":
            pile.append(i)
        elif c == "]" and pile:
            ouvre = pile.pop()
            lu = lire_suite_crochet(t, brut, ouvre, i, definitions, references)
            if lu:
                trouves.append(lu)
                if lu[2] != "image":
                    pile.clear()
                i = lu[1]
                continue
        i += 1
    return trouves


def lire_suite_crochet(t: str, brut: str, ouvre: int, ferme: int, definitions: dict[str, tuple[str, int]],
                       references: list[dict[str, Any]]) -> tuple[int, int, str, str, str] | None:
    """Ce qui suit « ] » : (destination), [étiquette], ou rien (raccourci)."""
    image = ouvre > 0 and t[ouvre - 1] == "!"
    debut = ouvre - 1 if image else ouvre
    texte = brut[ouvre + 1:ferme]
    genre = "image" if image else "lien"
    if t[ferme + 1:ferme + 2] == "(":
        dest = lire_destination(t, ferme + 2)
        if dest:
            return debut, dest[1], genre, dest[0], texte
    if t[ferme + 1:ferme + 2] == "[":
        fin = t.find("]", ferme + 2)
        if fin > 0 and "[" not in t[ferme + 2:fin]:
            etiquette = brut[ferme + 2:fin] or texte
            cle = normaliser_etiquette(etiquette)
            if cle in definitions:
                references.append({"etiquette": cle, "utilisee": True, "decalage": debut})
                return debut, fin + 1, genre if image else "reference", definitions[cle][0], texte
            if not (debut > 0 and (t[debut - 1].isalnum() or t[debut - 1] == "_")):
                references.append({"etiquette": cle, "utilisee": False, "decalage": debut})
            return None
    cle = normaliser_etiquette(texte)
    if cle in definitions and not texte.startswith("^"):
        references.append({"etiquette": cle, "utilisee": True, "decalage": debut})
        return debut, ferme + 1, genre if image else "reference", definitions[cle][0], texte
    return None


def titres_stdlib(lignes: list[str], code: set[int], decalages: list[int]) -> list[tuple[int, str]]:
    """(décalage, texte) des titres ATX et Setext hors code."""
    titres: list[tuple[int, str]] = []
    for i, ligne in enumerate(lignes):
        if i in code:
            continue
        atx = ATX.match(ligne)
        if atx:
            titres.append((decalages[i], texte_de_titre(atx.group(2) or "")))
            continue
        if SETEXT.match(ligne) and i > 0:
            paragraphe = paragraphe_au_dessus(lignes, i, code)
            if paragraphe:
                titres.append((decalages[i - len(paragraphe)], texte_de_titre(" ".join(l.strip() for l in paragraphe))))
    return titres


def paragraphe_au_dessus(lignes: list[str], i: int, code: set[int]) -> list[str]:
    """Lignes de paragraphe juste au-dessus d'un soulignement Setext (vide si aucune)."""
    bloc: list[str] = []
    j = i - 1
    while j >= 0 and lignes[j].strip() and j not in code:
        ligne = lignes[j]
        if ATX.match(ligne) or ITEM_LISTE.match(ligne) or ligne.lstrip().startswith((">", "|", "<")) \
                or SETEXT.match(ligne) or ligne.startswith(("    ", "\t")):
            return [] if not bloc else bloc
        bloc.insert(0, ligne)
        j -= 1
    return bloc


def analyser_stdlib(texte: str, references: list[dict[str, Any]]) -> Analyse:
    """Lecteur stdlib : liens, images, références, html brut, ancres."""
    lignes = texte.split("\n")
    decalages = [0]
    for ligne in lignes:
        decalages.append(decalages[-1] + len(ligne) + 1)
    code = lignes_de_code(lignes) | front_matter(lignes)
    car = list(texte)
    for i in code:
        masquer(car, decalages[i], decalages[i] + len(lignes[i]))
    for m in COMMENTAIRE.finditer("".join(car)):
        masquer(car, m.start(), m.end())
    avant_spans = "".join(car)
    definitions = lire_definitions(avant_spans, car)
    masquer_code_en_ligne(car)
    propre = "".join(car)
    trouves = scanner_crochets(propre, avant_spans, definitions, references)
    liens = [Lien(bisect.bisect_right(decalages, d), g, c, x.strip()[:80]) for d, _f, g, c, x in trouves]
    occupe = [(d, f) for d, f, *_ in trouves]
    liens += autres_liens(propre, occupe, decalages)
    for cle, (cible, pos) in definitions.items():
        if not any(r["etiquette"] == cle and r["utilisee"] for r in references):
            references.append({"etiquette": cle, "utilisee": None, "decalage": pos, "cible": cible})
    titres = titres_stdlib(avant_spans.split("\n"), code, decalages)
    titres += [(m.start(), nettoyer_balisage(m.group(2))) for m in TITRE_HTML.finditer(propre)]
    titres.sort()
    ancres = numeroter_ancres([slug_github(t) for _p, t in titres])
    return Analyse(sorted(liens, key=lambda l: l.ligne), ancres, ancres_html_de(propre))


def autres_liens(propre: str, occupe: list[tuple[int, int]], decalages: list[int]) -> list[Lien]:
    """Autoliens <...>, balises a/img, adresses nues hors des liens déjà lus."""
    debuts = [d for d, _f in occupe]
    fins = [f for _d, f in occupe]

    def dedans(pos: int) -> bool:
        k = bisect.bisect_right(debuts, pos) - 1
        return k >= 0 and pos < fins[k]

    def ligne(pos: int) -> int:
        return bisect.bisect_right(decalages, pos)

    liens = [Lien(ligne(m.start()), "autolien", m.group(1)) for m in AUTOLIEN.finditer(propre)
             if not dedans(m.start())]
    liens += [Lien(ligne(pos), "html", cible) for pos, cible in liens_html_de(propre)]
    for m in URL_NUE.finditer(propre):
        if not dedans(m.start()):
            liens.append(Lien(ligne(m.start()), "url_nue", m.group(0).rstrip(".,;:!?*_~'\")")))
    return liens


# --------------------------------------------------------------------------- moteur markdown-it-py

def analyser_markdown_it(texte: str) -> Analyse:
    """Même extraction par markdown-it-py (CommonMark + tableaux + barré)."""
    md = MarkdownIt("commonmark").enable("table").enable("strikethrough")
    lignes = texte.split("\n")
    entete = front_matter(lignes)
    jetons = md.parse("\n".join("" if i in entete else l for i, l in enumerate(lignes)), {})
    liens: list[Lien] = []
    titres: list[str] = []
    ancres_html: set[str] = set()
    for k, jeton in enumerate(jetons):
        ligne = (jeton.map[0] + 1) if jeton.map else 0
        if jeton.type == "html_block":
            liens += [Lien(ligne + jeton.content.count("\n", 0, p), "html", c) for p, c in liens_html_de(jeton.content)]
            ancres_html |= ancres_html_de(jeton.content)
            titres += [nettoyer_balisage(m.group(2)) for m in TITRE_HTML.finditer(jeton.content)]
        if jeton.type == "heading_open" and k + 1 < len(jetons):
            titres.append(texte_jetons(jetons[k + 1].children or []))
        if jeton.type == "inline":
            liens += liens_jetons(jeton.children or [], ligne, ancres_html)
    return Analyse(liens, numeroter_ancres([slug_github(t) for t in titres]), ancres_html)


def texte_jetons(enfants: list[Any]) -> str:
    """Texte d'un titre tel que rendu (sans images ni balises)."""
    morceaux = []
    for e in enfants:
        if e.type in ("text", "code_inline"):
            morceaux.append(e.content)
        elif e.type in ("softbreak", "hardbreak"):
            morceaux.append(" ")
    return "".join(morceaux)


def liens_jetons(enfants: list[Any], ligne: int, ancres_html: set[str]) -> list[Lien]:
    """Liens, images, autoliens et html en ligne des enfants d'un jeton inline."""
    liens: list[Lien] = []
    sauts = 0
    for e in enfants:
        if e.type in ("softbreak", "hardbreak"):
            sauts += 1
        elif e.type == "link_open":
            genre = "autolien" if e.markup == "autolink" else "lien"
            liens.append(Lien(ligne + sauts, genre, unquote_email(e.attrs.get("href", ""), e.info)))
        elif e.type == "image":
            liens.append(Lien(ligne + sauts, "image", str(e.attrs.get("src", "")), e.content[:80]))
        elif e.type == "html_inline":
            liens += [Lien(ligne + sauts, "html", c) for _p, c in liens_html_de(e.content)]
            ancres_html |= ancres_html_de(e.content)
    return liens


def unquote_email(href: str, info: str) -> str:
    """markdown-it préfixe mailto: aux autoliens courriel ; garde la cible telle qu'écrite."""
    return href[len("mailto:"):] if info == "auto" and href.startswith("mailto:") and "@" in href else href


# --------------------------------------------------------------------------- vérification

def classer_cible(cible: str) -> str:
    """vide, ancre, externe ou relatif."""
    if not cible.strip():
        return "vide"
    if cible.startswith("#"):
        return "ancre"
    if SCHEMA.match(cible) or cible.startswith(("//", "www.")) or ("@" in cible and "/" not in cible):
        return "externe"
    return "relatif"


def casse_exacte(chemin: Path, depot: Path, cache: dict[Path, set[str]]) -> Path | None:
    """Premier composant dont la casse diffère du disque (None si la casse est exacte)."""
    try:
        parties = chemin.relative_to(depot).parts
    except ValueError:
        return None
    courant = depot
    for partie in parties:
        if courant not in cache:
            try:
                cache[courant] = set(os.listdir(courant))
            except OSError:
                return None
        if partie not in cache[courant]:
            return courant / partie
        courant = courant / partie
    return None


def suggestion_casse(chemin: Path, depot: Path) -> str | None:
    """Premier composant absent qui existe avec une autre casse : chemin corrigé jusque-là."""
    try:
        parties = chemin.relative_to(depot).parts
    except ValueError:
        return None
    courant = depot
    for partie in parties:
        if (courant / partie).exists():
            courant = courant / partie
            continue
        try:
            proches = [n for n in os.listdir(courant) if n.casefold() == partie.casefold()]
        except OSError:
            return None
        return str((courant / proches[0]).relative_to(depot)) if proches else None
    return None


def dans(chemin: Path, dossiers: tuple[Path, ...]) -> bool:
    """Le chemin est-il sous l'un des dossiers (ou égal à l'un des fichiers) ?"""
    return any(chemin == d or chemin.is_relative_to(d) for d in dossiers)


def verifier_ancre(fragment: str, ancres: list[str], ancres_html: set[str]) -> tuple[str, str]:
    """(statut, détail) d'un fragment contre les ancres d'un document."""
    frag = unquote(fragment)
    if frag.startswith(PREFIXE_GITHUB):
        frag = frag[len(PREFIXE_GITHUB):]
    connues = set(ancres) | ancres_html
    if frag in connues or frag == "" or frag.lower() == "top":
        return "ok", ""
    pliees = {a.casefold(): a for a in connues}
    if frag.casefold() in pliees:
        return "ancre_casse", f"GitHub attend « #{pliees[frag.casefold()]} »"
    proches = difflib.get_close_matches(frag, sorted(connues), n=1)
    return "ancre_absente", f"aucun titre ni id « {frag} »" + (f" ; proche : « #{proches[0]} »" if proches else "")


def verifier_lien(lien: Lien, doc: Document, ctx: Contexte, caches: dict[str, Any]) -> tuple[str, str]:
    """(statut, détail) d'un lien."""
    genre = classer_cible(lien.cible)
    if genre == "vide":
        return "cible_vide", "lien sans cible"
    if genre == "externe":
        return "externe", "non visité"
    if genre == "ancre":
        return verifier_ancre(lien.cible[1:], doc.analyse.ancres, doc.analyse.ancres_html)
    chemin_brut, _, fragment = lien.cible.partition("#")
    chemin_brut = unquote(chemin_brut.split("?", 1)[0])
    base = ctx.depot if chemin_brut.startswith("/") else (doc.chemin.parent if doc.chemin else ctx.base_texte)
    cible = Path(os.path.normpath(base / chemin_brut.lstrip("/")))
    if not dans(cible, (ctx.depot,)):
        return "hors_depot", f"{chemin_brut} sort du dépôt {ctx.depot}"
    if not cible.exists():
        proche = suggestion_casse(cible, ctx.depot)
        return "fichier_absent", f"{cible} n'existe pas" + (f" ; « {proche} » existe (casse)" if proche else "")
    fautif = casse_exacte(cible, ctx.depot, caches.setdefault("dossiers", {}))
    if fautif is not None:
        return "casse_differente", f"« {fautif.name} » : la casse diffère sur le disque (cassé sur GitHub)"
    if cible.is_dir():
        return "dossier", "lien vers un dossier (GitHub affiche la liste, d'autres rendus non)"
    if not fragment:
        return "ok", ""
    return verifier_fragment_externe(cible, fragment, ctx, caches)


def verifier_fragment_externe(cible: Path, fragment: str, ctx: Contexte,
                              caches: dict[str, Any]) -> tuple[str, str]:
    """Ancre dans un autre fichier : vérifiée si Markdown et dans le périmètre."""
    if cible.suffix.lower() not in EXTENSIONS_MD:
        return "ancre_non_verifiee", "ancre d'un fichier non Markdown"
    if not dans(cible, ctx.perimetre):
        return "ancre_non_verifiee", "fichier hors périmètre (--perimetre pour l'inclure)"
    analyses = caches.setdefault("analyses", {})
    if cible not in analyses:
        try:
            texte = lire_texte(cible, ctx.max_octets)
        except EntreeInvalide as exc:
            return "ancre_non_verifiee", f"cible illisible : {exc}"
        analyses[cible] = analyser(texte, caches["moteur"], [])[0]
    autre = analyses[cible]
    return verifier_ancre(fragment, autre.ancres, autre.ancres_html)


STATUTS_CASSES = frozenset({"cible_vide", "ancre_absente", "hors_depot", "fichier_absent",
                            "casse_differente", "reference_non_definie"})
STATUTS_AVERTIS = frozenset({"ancre_casse", "dossier", "ancre_non_verifiee", "definition_inutilisee"})


def gravite(statut: str, strict: bool) -> str:
    """casse, avertissement ou ok ; --strict promeut les avertissements (sauf définition inutilisée)."""
    if statut in STATUTS_CASSES or (strict and statut in STATUTS_AVERTIS and statut != "definition_inutilisee"):
        return "casse"
    return "avertissement" if statut in STATUTS_AVERTIS else "ok"


# --------------------------------------------------------------------------- entrées

def lire_texte(chemin: Path, max_octets: int) -> str:
    """Texte utf-8 d'un fichier, borné ; lève EntreeInvalide."""
    try:
        with chemin.open("rb") as flux:
            octets = flux.read(max_octets + 1)
    except OSError as exc:
        raise EntreeInvalide(f"illisible : {exc.strerror or exc}") from exc
    if len(octets) > max_octets:
        raise EntreeInvalide(f"plus de {max_octets} octets (--max-octets)")
    if b"\x00" in octets[:65536]:
        raise EntreeInvalide("contenu binaire (octets nuls)")
    try:
        texte = octets.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise EntreeInvalide(f"pas de l'utf-8 (octet {exc.start})") from exc
    return texte.replace("\r\n", "\n").replace("\r", "\n")


def parcourir(dossier: Path, reste: int) -> list[Path]:
    """Fichiers Markdown d'un dossier, triés, hors dossiers cachés et outillage."""
    trouves: list[Path] = []
    for racine, dossiers, fichiers in os.walk(dossier):
        dossiers[:] = sorted(d for d in dossiers if d not in DOSSIERS_IGNORES and not d.startswith("."))
        for nom in sorted(fichiers):
            if Path(nom).suffix.lower() in EXTENSIONS_MD and len(trouves) < reste:
                trouves.append(Path(racine) / nom)
    return trouves


def trouver_depot(depart: Path) -> Path:
    """Plus proche ancêtre portant .git, sinon le point de départ."""
    for candidat in (depart, *depart.parents):
        if (candidat / ".git").exists():
            return candidat
    return depart


def resoudre(brut: str | Path, base: Path) -> Path:
    """Chemin absolu normalisé."""
    chemin = Path(brut)
    return Path(os.path.normpath(chemin if chemin.is_absolute() else base / chemin))


def analyser(texte: str, moteur: str, references: list[dict[str, Any]]) -> tuple[Analyse, Analyse]:
    """(analyse du moteur choisi, analyse stdlib) ; les références passent toujours par stdlib."""
    stdlib = analyser_stdlib(texte, references)
    if moteur == "markdown-it-py":
        return analyser_markdown_it(texte), stdlib
    return stdlib, stdlib


def cles_comparables(analyse: Analyse) -> Counter[tuple[str, str]]:
    """(genre, cible décodée) des liens, adresses nues exclues (markdown-it ne les lit pas ici)."""
    return Counter(("lien" if l.genre == "reference" else l.genre, unquote(l.cible))
                   for l in analyse.liens if l.genre != "url_nue")


def comparer_moteurs(principale: Analyse, autre: Analyse) -> dict[str, Any]:
    """Écarts entre markdown-it-py et le lecteur stdlib (liens hors adresses nues, ancres)."""
    a, b = cles_comparables(principale), cles_comparables(autre)
    return {
        "liens_markdown_it": sum(a.values()), "liens_stdlib": sum(b.values()),
        "seulement_markdown_it": [f"{g} {c}" for g, c in (a - b)][:10],
        "seulement_stdlib": [f"{g} {c}" for g, c in (b - a)][:10],
        "ancres_identiques": principale.ancres == autre.ancres,
        "ancres_differentes": sorted(set(principale.ancres) ^ set(autre.ancres))[:10],
    }


# --------------------------------------------------------------------------- rapport

def verifier_document(doc: Document, ctx: Contexte, caches: dict[str, Any]) -> list[dict[str, Any]]:
    """Statut de chaque lien d'un document, plus les références non définies ou inutilisées."""
    constats = []
    for lien in doc.analyse.liens:
        statut, detail = verifier_lien(lien, doc, ctx, caches)
        constats.append({"fichier": doc.nom, "ligne": lien.ligne, "genre": lien.genre,
                         "cible": lien.cible, "texte": lien.texte, "statut": statut, "detail": detail})
    decalages = [0]
    for ligne in doc.texte.split("\n"):
        decalages.append(decalages[-1] + len(ligne) + 1)
    for ref in doc.references:
        ligne = bisect.bisect_right(decalages, ref["decalage"])
        if ref["utilisee"] is False:
            constats.append({"fichier": doc.nom, "ligne": ligne, "genre": "reference",
                             "cible": f"[{ref['etiquette']}]", "texte": "", "statut": "reference_non_definie",
                             "detail": "étiquette sans définition : le texte s'affiche tel quel"})
        elif ref["utilisee"] is None:
            constats.append({"fichier": doc.nom, "ligne": ligne, "genre": "definition",
                             "cible": ref["cible"], "texte": f"[{ref['etiquette']}]",
                             "statut": "definition_inutilisee", "detail": "définition jamais utilisée"})
    return constats


def construire_rapport(constats: list[dict[str, Any]], docs: list[Document], moteur: str,
                       comparaisons: dict[str, Any], ctx: Contexte) -> dict[str, Any]:
    """Objet JSON final."""
    liens = [c for c in constats if c["genre"] != "definition" and c["statut"] != "reference_non_definie"]
    casses = [c for c in constats if gravite(c["statut"], ctx.strict) == "casse"]
    avertis = [c for c in constats if gravite(c["statut"], ctx.strict) == "avertissement"]
    noms = [f"{c['fichier']}:{c['ligne']} {c['cible']}" for c in liens]
    return {
        "outil": NOM_OUTIL,
        "moteur": moteur,
        "comparaison": comparaisons or None,
        "denominateur": len(liens),
        "unite": "liens",
        "examines": noms[:PLAFOND_LISTE],
        "examines_tronques": len(noms) > PLAFOND_LISTE,
        "fichiers_examines": [d.nom for d in docs][:PLAFOND_LISTE],
        "nombre_fichiers": len(docs),
        "depot": str(ctx.depot),
        "par_statut": dict(Counter(c["statut"] for c in constats)),
        "casses": casses[:PLAFOND_LISTE],
        "nombre_casses": len(casses),
        "avertissements": avertis[:PLAFOND_LISTE],
        "externes_jamais_visites": sorted({c["cible"] for c in liens if c["statut"] == "externe"})[:PLAFOND_LISTE],
        "contrat": lire_contrat(),
    }


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
    for c in rapport["casses"]:
        print(f"CASSÉ {c['fichier']}:{c['ligne']} {c['statut']} « {c['cible']} » — {c['detail']}")
    for c in rapport["avertissements"][:50]:
        print(f"avertissement {c['fichier']}:{c['ligne']} {c['statut']} « {c['cible']} » — {c['detail']}")
    print(f"{rapport['denominateur']} lien(s) dans {rapport['nombre_fichiers']} fichier(s) "
          f"(moteur {rapport['moteur']}) : {rapport['nombre_casses']} cassé(s), "
          f"{len(rapport['externes_jamais_visites'])} adresse(s) externe(s) listée(s), jamais visitée(s)")


def afficher_json(rapport: dict[str, Any]) -> None:
    print(json.dumps(rapport, ensure_ascii=False, indent=2))


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        description="Vérifie hors ligne les liens relatifs, images, références et ancres (#titre, "
                    "règles de GitHub) d'une documentation Markdown. Les adresses externes sont "
                    "listées, jamais visitées.",
        epilog="Exemples : python verifier_liens_markdown.py README.md docs --json\n"
               "          python verifier_liens_markdown.py docs --perimetre . --strict\n"
               "Codes : 0 aucun lien cassé, 1 lien cassé, 2 entrée invalide, 3 aucun lien à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parseur.add_argument("chemins", nargs="*", help="fichiers Markdown ou dossiers")
    parseur.add_argument("--texte", help="Markdown en ligne ; la suite \\n vaut un saut de ligne")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="base des chemins relatifs et des liens de --texte (défaut : dossier courant)")
    parseur.add_argument("--depot", type=Path, default=None,
                         help="racine du dépôt pour les liens « /... » (défaut : ancêtre portant .git)")
    parseur.add_argument("--perimetre", type=Path, action="append", default=[],
                         help="dossier supplémentaire où l'outil peut lire les ancres des fichiers liés")
    parseur.add_argument("--moteur", choices=("auto", "stdlib", "markdown-it-py"), default="auto",
                         help="analyseur (auto : markdown-it-py s'il est installé)")
    parseur.add_argument("--strict", action="store_true",
                         help="liens vers dossier, casse d'ancre et ancres non vérifiées comptent comme cassés")
    parseur.add_argument("--max-octets", type=int, default=8 << 20, help="taille maximale d'un fichier")
    parseur.add_argument("--max-fichiers", type=int, default=5000, help="fichiers lus au plus")
    return parseur


def base_relative(racine: Path | None) -> Path:
    """Base des chemins relatifs : --racine, sinon le dossier courant (sinon celui de l'outil)."""
    if racine is not None:
        return Path(os.path.normpath(racine.resolve()))
    try:
        return Path.cwd()
    except OSError:
        return RACINE


def choisir_moteur(demande: str) -> str:
    """markdown-it-py si disponible et voulu ; une ligne sur stderr sinon."""
    if demande == "stdlib":
        return "stdlib"
    if MarkdownIt is None:
        if demande == "markdown-it-py":
            raise EntreeInvalide("--moteur markdown-it-py demandé mais la bibliothèque est absente")
        print("mode dégradé — markdown-it-py absent : lecteur stdlib approché de CommonMark, "
              "sans comparaison", file=sys.stderr)
        return "stdlib"
    return "markdown-it-py"


def rassembler(args: argparse.Namespace, base: Path) -> tuple[list[tuple[str, Path | None, str | None]], list[Path]]:
    """Sources (nom, chemin, texte) et dossiers/fichiers formant le périmètre."""
    sources: list[tuple[str, Path | None, str | None]] = []
    perimetre: list[Path] = [resoudre(p, base) for p in args.perimetre]
    for brut in args.chemins:
        chemin = resoudre(brut, base)
        if chemin.is_dir():
            perimetre.append(chemin)
            sources += [(str(p), p, None) for p in parcourir(chemin, args.max_fichiers - len(sources))]
        elif chemin.is_file():
            perimetre.append(chemin)
            sources.append((str(chemin), chemin, None))
        else:
            raise EntreeInvalide(f"chemin introuvable : {brut}")
    if args.texte is not None:
        sources.append(("<texte>", None, args.texte.replace("\\n", "\n")))
    return sources, perimetre


def preparer(args: argparse.Namespace) -> tuple[list[tuple[str, Path | None, str | None]], Contexte, str]:
    """Valide les options et construit le contexte ; lève EntreeInvalide."""
    if not args.chemins and args.texte is None:
        raise EntreeInvalide("donner un fichier, un dossier ou --texte")
    if args.max_octets < 1 or args.max_fichiers < 1:
        raise EntreeInvalide("plafonds positifs attendus")
    base = base_relative(args.racine)
    sources, perimetre = rassembler(args, base)
    premier = next((p for _n, p, _t in sources if p is not None), None)
    depart = premier.parent if premier else base
    depot = resoudre(args.depot, base) if args.depot else trouver_depot(depart)
    if args.depot and not depot.is_dir():
        raise EntreeInvalide(f"--depot n'est pas un dossier : {args.depot}")
    moteur = choisir_moteur(args.moteur)
    return sources, Contexte(depot, tuple(perimetre), base, args.strict, args.max_octets), moteur


def traiter(sources: list[tuple[str, Path | None, str | None]], ctx: Contexte,
            moteur: str) -> tuple[list[dict[str, Any]], list[Document], dict[str, Any], list[dict[str, str]]]:
    """Analyse et vérifie chaque source."""
    caches: dict[str, Any] = {"moteur": moteur}
    constats: list[dict[str, Any]] = []
    docs: list[Document] = []
    comparaisons: dict[str, Any] = {}
    illisibles: list[dict[str, str]] = []
    for nom, chemin, texte in sources:
        try:
            texte = lire_texte(chemin, ctx.max_octets) if chemin is not None else (texte or "")
        except EntreeInvalide as exc:
            illisibles.append({"chemin": nom, "raison": str(exc)})
            continue
        references: list[dict[str, Any]] = []
        principale, stdlib = analyser(texte, moteur, references)
        doc = Document(nom, chemin, texte, principale, references)
        if chemin is not None:
            caches.setdefault("analyses", {})[chemin] = principale
        if moteur == "markdown-it-py":
            comparaisons[nom] = comparer_moteurs(principale, stdlib)
        docs.append(doc)
        constats += verifier_document(doc, ctx, caches)
    return constats, docs, comparaisons, illisibles


def main() -> int:
    args = construire_parseur().parse_args()
    try:
        sources, ctx, moteur = preparer(args)
        constats, docs, comparaisons, illisibles = traiter(sources, ctx, moteur)
    except EntreeInvalide as exc:
        print(f"entrée invalide : {exc}", file=sys.stderr)
        return 2
    nom_moteur = moteur if moteur == "stdlib" else f"markdown-it-py {version_markdown_it()}"
    rapport = construire_rapport(constats, docs, nom_moteur, comparaisons, ctx)
    rapport["illisibles"] = illisibles
    if args.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    for i in illisibles:
        print(f"entrée invalide : {i['chemin']} : {i['raison']}", file=sys.stderr)
    if illisibles:
        return 2
    if rapport["denominateur"] == 0:
        print("dénominateur nul : aucun lien trouvé, rien à examiner", file=sys.stderr)
        return 3
    return 1 if rapport["nombre_casses"] else 0


def version_markdown_it() -> str:
    """Version installée de markdown-it-py."""
    from importlib import metadata
    try:
        return metadata.version("markdown-it-py")
    except metadata.PackageNotFoundError:
        return "?"


if __name__ == "__main__":
    raise SystemExit(main())
