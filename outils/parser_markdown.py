#!/usr/bin/env python3.14
"""
QUESTION      Quelle est la structure de ce document Markdown ?
MESURE        Extraction des titres, listes, blocs de code, liens, images, et métadonnées
              via expressions régulières et html.parser pour les balises HTML inline.
HYPOTHÈSES    Le document est un Markdown valide (CommonMark ou GFM).
LIMITES       Ne gère pas les extensions CommonMark avancées (tableaux, footnotes, etc.).
              Ne valide pas la syntaxe Markdown, seulement extrait les éléments visibles.
CONTRE-EXEMPLES Un lien avec un titre contenant des crochets imbriqués peut être mal parsé.
INVOCATION
    {outil} {fichier} --json
DOMAINE       Fichiers Markdown (.md, .markdown) de moins de 10 Mo.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from html.parser import HTMLParser

__all__ = [
    "analyser_markdown",
    "main",
]

RACINE = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ------------------------------------------------------------------
# Expressions régulières pour les éléments Markdown
# ------------------------------------------------------------------

RE_TITRE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+\s*)?$", re.MULTILINE)
RE_LISTE_UL = re.compile(r"^([\*\-\+])\s+(.+)$", re.MULTILINE)
RE_LISTE_OL = re.compile(r"^(\d+)\.\s+(.+)$", re.MULTILINE)
RE_BLOC_CODE = re.compile(r"^```(\w*)\s*$.*?^```$", re.MULTILINE | re.DOTALL)
RE_LIEN_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)")
RE_LIEN = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"([^\"]*)\")?\)")
RE_HTML_INLINE = re.compile(r"<([a-zA-Z]+)(?:\s+[^>]*)?>(.*?)</\1>", re.DOTALL)
RE_METADATA = re.compile(r"^---[\s\S]+?---$", re.MULTILINE)
RE_CITATION = re.compile(r"^>\s+(.+)$", re.MULTILINE)
RE_REGLE_HR = re.compile(r"^([-*_])(\s*\1){2,}\s*$", re.MULTILINE)

# ------------------------------------------------------------------
# Analyseur HTML inline (balises simples uniquement)
# ------------------------------------------------------------------

class AnalyseurHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.balises: List[Tuple[str, Dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        self.balises.append((tag, dict(attrs)))

    def handle_endtag(self, tag: str) -> None:
        pass

    def handle_data(self, data: str) -> None:
        pass

# ------------------------------------------------------------------
# Fonctions d'analyse
# ------------------------------------------------------------------

def extraire_titres(contenu: str) -> List[Dict[str, Union[int, str]]]:
    titres = []
    for match in RE_TITRE.finditer(contenu):
        niveau = len(match.group(1))
        texte = match.group(2).strip()
        titres.append({"niveau": niveau, "texte": texte})
    return titres

def extraire_listes(contenu: str) -> Dict[str, List[Dict[str, Union[str, int]]]]:
    listes = {"ul": [], "ol": []}
    for match in RE_LISTE_UL.finditer(contenu):
        listes["ul"].append({"marqueur": match.group(1), "texte": match.group(2).strip()})
    for match in RE_LISTE_OL.finditer(contenu):
        listes["ol"].append({"position": int(match.group(1)), "texte": match.group(2).strip()})
    return listes

def extraire_blocs_code(contenu: str) -> List[Dict[str, str]]:
    blocs = []
    for match in RE_BLOC_CODE.finditer(contenu):
        langage = match.group(1).strip()
        code = match.group(0).split("\n", 1)[1].rsplit("\n", 1)[0].strip()
        blocs.append({"langage": langage, "code": code})
    return blocs

def extraire_liens_images(contenu: str) -> Dict[str, List[Dict[str, str]]]:
    liens_images = {"liens": [], "images": []}
    for match in RE_LIEN_IMAGE.finditer(contenu):
        liens_images["images"].append({
            "texte": match.group(1),
            "url": match.group(2),
            "titre": match.group(3) or "",
        })
    for match in RE_LIEN.finditer(contenu):
        liens_images["liens"].append({
            "texte": match.group(1),
            "url": match.group(2),
            "titre": match.group(3) or "",
        })
    return liens_images

def extraire_html_inline(contenu: str) -> List[Dict[str, Any]]:
    balises = []
    for match in RE_HTML_INLINE.finditer(contenu):
        tag = match.group(1).lower()
        texte = match.group(2).strip()
        parser = AnalyseurHTML()
        parser.feed(match.group(0))
        balises.append({"tag": tag, "texte": texte, "attrs": parser.balises[0][1] if parser.balises else {}})
    return balises

def extraire_metadata(contenu: str) -> Optional[Dict[str, str]]:
    match = RE_METADATA.search(contenu)
    if not match:
        return None
    metadata = {}
    for ligne in match.group(0).split("\n")[1:-1]:
        if ":" in ligne:
            cle, valeur = ligne.split(":", 1)
            metadata[cle.strip()] = valeur.strip()
    return metadata

def extraire_citations(contenu: str) -> List[str]:
    citations = []
    for match in RE_CITATION.finditer(contenu):
        citations.append(match.group(1).strip())
    return citations

def extraire_regles_hr(contenu: str) -> List[str]:
    regles = []
    for match in RE_REGLE_HR.finditer(contenu):
        regles.append(match.group(0).strip())
    return regles

def construire_sortie_json(denominateur: int, examines: List[Dict], tronque: bool) -> Dict[str, Any]:
    """Construit la sortie JSON avec toutes les clés obligatoires."""
    return {
        "denominateur": denominateur,
        "examines": examines,
        "examines_tronques": tronque,
        "contrat": {
            "QUESTION": "Quelle est la structure de ce document Markdown ?",
            "MESURE": "Extraction des titres, listes, blocs de code, liens, images, et métadonnées via expressions régulières et html.parser pour les balises HTML inline.",
            "HYPOTHÈSES": "Le document est un Markdown valide (CommonMark ou GFM).",
            "LIMITES": "Ne gère pas les extensions CommonMark avancées (tableaux, footnotes, etc.). Ne valide pas la syntaxe Markdown, seulement extrait les éléments visibles.",
            "CONTRE-EXEMPLES": "Un lien avec un titre contenant des crochets imbriqués peut être mal parsé.",
            "DOMAINE": "Fichiers Markdown (.md, .markdown) de moins de 10 Mo.",
        },
    }

def analyser_markdown(contenu: str) -> Dict[str, Any]:
    structure = {
        "titres": extraire_titres(contenu),
        "listes": extraire_listes(contenu),
        "blocs_code": extraire_blocs_code(contenu),
        "liens_images": extraire_liens_images(contenu),
        "html_inline": extraire_html_inline(contenu),
        "metadata": extraire_metadata(contenu),
        "citations": extraire_citations(contenu),
        "regles_horizontales": extraire_regles_hr(contenu),
    }

    denominateur = 0
    for elements in structure.values():
        if isinstance(elements, list):
            denominateur += len(elements)
        elif isinstance(elements, dict):
            denominateur += sum(len(v) for v in elements.values())

    elements_nommes = [
        ("titres", structure["titres"]),
        ("listes_ul", structure["listes"]["ul"]),
        ("listes_ol", structure["listes"]["ol"]),
        ("blocs_code", structure["blocs_code"]),
        ("liens", structure["liens_images"]["liens"]),
        ("images", structure["liens_images"]["images"]),
        ("html_inline", structure["html_inline"]),
        ("citations", structure["citations"]),
        ("regles_horizontales", structure["regles_horizontales"]),
    ]

    if structure["metadata"]:
        elements_nommes.append(("metadata", [structure["metadata"]]))

    examines = []
    tronque = False
    for nom, elements in elements_nommes:
        if len(elements) > 200:
            examines.extend([{nom: elem} for elem in elements[:200]])
            tronque = True
        else:
            examines.extend([{nom: elem} for elem in elements])

    return construire_sortie_json(denominateur, examines, tronque)

# ------------------------------------------------------------------
# Mode dégradé avec markdown-it-py (si disponible)
# ------------------------------------------------------------------

def analyser_markdown_avec_markdown_it(contenu: str) -> Dict[str, Any]:
    try:
        from markdown_it import MarkdownIt
        from markdown_it.tree import SyntaxTreeNode
    except ImportError:
        return analyser_markdown(contenu)

    md = MarkdownIt("commonmark")
    tokens = md.parse(contenu)
    root = SyntaxTreeNode(tokens)

    structure = {
        "titres": [],
        "listes": {"ul": [], "ol": []},
        "blocs_code": [],
        "liens_images": {"liens": [], "images": []},
        "html_inline": [],
        "metadata": None,
        "citations": [],
        "regles_horizontales": [],
    }

    for node in root.walk():
        if node.type == "heading":
            structure["titres"].append({
                "niveau": node.level,
                "texte": node.children[0].content if node.children else "",
            })
        elif node.type == "bullet_list":
            for item in node.children:
                if item.children:
                    texte = " ".join(c.content for c in item.children if c.type == "inline")
                    structure["listes"]["ul"].append({"marqueur": "*", "texte": texte})
        elif node.type == "ordered_list":
            for item in node.children:
                if item.children:
                    texte = " ".join(c.content for c in item.children if c.type == "inline")
                    structure["listes"]["ol"].append({"position": item.attrs.get("start", 1), "texte": texte})
        elif node.type == "fence":
            structure["blocs_code"].append({
                "langage": node.info or "",
                "code": node.content,
            })
        elif node.type == "link":
            structure["liens_images"]["liens"].append({
                "texte": node.content,
                "url": node.attrs.get("href", ""),
                "titre": node.attrs.get("title", ""),
            })
        elif node.type == "image":
            structure["liens_images"]["images"].append({
                "texte": node.content,
                "url": node.attrs.get("src", ""),
                "titre": node.attrs.get("title", ""),
            })
        elif node.type == "html_inline":
            structure["html_inline"].append({
                "tag": node.content.split()[0][1:],
                "texte": node.content,
                "attrs": {},
            })
        elif node.type == "blockquote":
            for child in node.children:
                if child.type == "paragraph":
                    structure["citations"].append(child.content)
        elif node.type == "hr":
            structure["regles_horizontales"].append("---")

    denominateur = 0
    for elements in structure.values():
        if isinstance(elements, list):
            denominateur += len(elements)
        elif isinstance(elements, dict):
            denominateur += sum(len(v) for v in elements.values())

    elements_nommes = [
        ("titres", structure["titres"]),
        ("listes_ul", structure["listes"]["ul"]),
        ("listes_ol", structure["listes"]["ol"]),
        ("blocs_code", structure["blocs_code"]),
        ("liens", structure["liens_images"]["liens"]),
        ("images", structure["liens_images"]["images"]),
        ("html_inline", structure["html_inline"]),
        ("citations", structure["citations"]),
        ("regles_horizontales", structure["regles_horizontales"]),
    ]

    examines = []
    tronque = False
    for nom, elements in elements_nommes:
        if len(elements) > 200:
            examines.extend([{nom: elem} for elem in elements[:200]])
            tronque = True
        else:
            examines.extend([{nom: elem} for elem in elements])

    return construire_sortie_json(denominateur, examines, tronque)

# ------------------------------------------------------------------
# Interface CLI
# ------------------------------------------------------------------

def lire_fichier(chemin: Path) -> str:
    try:
        return chemin.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            return chemin.read_text(encoding="latin1")
        except Exception as e:
            sys.stderr.write(f"Erreur de lecture du fichier : {e}\n")
            raise SystemExit(2)
    except Exception as e:
        sys.stderr.write(f"Erreur : {e}\n")
        raise SystemExit(2)

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyser la structure d'un document Markdown.",
        epilog="Exemple : python parser_markdown.py document.md --json",
    )
    parser.add_argument(
        "fichier",
        type=Path,
        help="Chemin vers le fichier Markdown à analyser.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie au format JSON sur stdout.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Répertoire racine pour les imports relatifs (défaut : répertoire du script).",
    )

    args = parser.parse_args()

    if not args.fichier.is_file():
        if args.json:
            sortie = construire_sortie_json(0, [], False)
            sortie["erreur"] = f"Fichier '{args.fichier}' introuvable"
            json.dump(sortie, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            sys.stderr.write(f"Erreur : le fichier '{args.fichier}' n'existe pas ou n'est pas lisible.\n")
        return 2

    try:
        contenu = lire_fichier(args.fichier)
    except Exception:
        if args.json:
            sortie = construire_sortie_json(0, [], False)
            sortie["erreur"] = "Erreur de lecture du fichier"
            json.dump(sortie, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        return 2

    if args.racine not in sys.path:
        sys.path.insert(0, str(args.racine))

    try:
        structure = analyser_markdown_avec_markdown_it(contenu)
        mode = "markdown-it-py (mode complet)"
    except ImportError:
        structure = analyser_markdown(contenu)
        mode = "stdlib (mode dégradé)"
        if not args.json:
            sys.stderr.write("Avertissement : markdown-it-py non disponible, utilisation du mode dégradé.\n")

    if structure["denominateur"] == 0:
        if args.json:
            sys.stderr.write("Denominateur nul : rien à examiner, refus de conclure.\n")
            json.dump(structure, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            sys.stderr.write("Denominateur nul : rien à examiner, refus de conclure.\n")
        return 3

    if args.json:
        json.dump(structure, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        sys.stderr.write(f"Analyse effectuée avec {mode}.\n")
        sys.stderr.write(f"Éléments examinés : {structure['denominateur']}\n")
        if structure["examines_tronques"]:
            sys.stderr.write("Avertissement : certains éléments ont été tronqués (limite à 200).\n")

        for titre in structure["examines"]:
            if "titres" in titre:
                t = titre["titres"]
                sys.stdout.write(f"Titre {t['niveau']}: {t['texte']}\n")
        for liste in structure["examines"]:
            if "listes_ul" in liste:
                l = liste["listes_ul"]
                sys.stdout.write(f"Liste UL: {l['texte']}\n")
            elif "listes_ol" in liste:
                l = liste["listes_ol"]
                sys.stdout.write(f"Liste OL {l['position']}: {l['texte']}\n")
        for bloc in structure["examines"]:
            if "blocs_code" in bloc:
                b = bloc["blocs_code"]
                sys.stdout.write(f"Bloc de code ({b['langage']}):\n{b['code']}\n\n")
        for lien in structure["examines"]:
            if "liens" in lien:
                l = lien["liens"]
                sys.stdout.write(f"Lien: [{l['texte']}]({l['url']})\n")
            elif "images" in lien:
                i = lien["images"]
                sys.stdout.write(f"Image: ![{i['texte']}]({i['url']})\n")
        for html_elem in structure["examines"]:
            if "html_inline" in html_elem:
                h = html_elem["html_inline"]
                sys.stdout.write(f"HTML inline: <{h['tag']}>{h['texte']}</{h['tag']}>\n")
        for citation in structure["examines"]:
            if "citations" in citation:
                sys.stdout.write(f"Citation: {citation['citations']}\n")
        for regle in structure["examines"]:
            if "regles_horizontales" in regle:
                sys.stdout.write("Règle horizontale\n")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())