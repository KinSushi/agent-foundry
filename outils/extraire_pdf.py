"""
QUESTION      Que contient ce PDF (texte, structure, images) ?
MESURE        Extraction du texte, decompte des pages, des images et des objets via pymupdf ou analyse syntaxique basique en mode degrade.
HYPOTHESES    Le fichier est un PDF valide, non chiffre, accessible en lecture.
LIMITES       Sans pymupdf, l extraction du texte est limitee aux chaines simples (Tj/TJ) et les images sont seulement decomptees.
CONTRE-EXEMPLES Un PDF chiffre ou un fichier non PDF renvoie un resultat vide ou une erreur. Un PDF avec texte vectoriel complexe peut donner un texte vide en mode degrade.
DOMAINE       Fichiers PDF locaux.
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import argparse
import json
import re
import zlib
from pathlib import Path
from typing import Any

__all__ = ["analyser_pdf", "main"]

try:
    import pymupdf
    A_PYMUPDF = True
except ImportError:
    A_PYMUPDF = False

CONTRAT = {
    "QUESTION": "Que contient ce PDF (texte, structure, images) ?",
    "MESURE": "Extraction du texte, decompte des pages, des images et des objets via pymupdf ou analyse syntaxique basique en mode degrade.",
    "HYPOTHESES": "Le fichier est un PDF valide, non chiffre, accessible en lecture.",
    "LIMITES": "Sans pymupdf, l extraction du texte est limitee aux chaines simples (Tj/TJ) et les images sont seulement decomptees.",
    "CONTRE-EXEMPLES": "Un PDF chiffre ou un fichier non PDF renvoie un resultat vide ou une erreur. Un PDF avec texte vectoriel complexe peut donner un texte vide en mode degrade.",
    "DOMAINE": "Fichiers PDF locaux."
}

def _compter_pages_degrade(donnees: bytes) -> int:
    match = re.search(rb'/Type\s*/Pages[^>]*?/Count\s+(\d+)', donnees, re.DOTALL)
    if match:
        return int(match.group(1))
    return len(re.findall(rb'/Type\s*/Page(?!\w)', donnees))

def _compter_images_degrade(donnees: bytes) -> int:
    return len(re.findall(rb'/Subtype\s*/Image', donnees))

def _extraire_texte_degrade(donnees: bytes) -> str:
    morceaux = []
    idx = 0
    while True:
        debut = donnees.find(b"stream", idx)
        if debut == -1:
            break
        debut += 6
        if donnees[debut:debut+2] == b"\r\n":
            debut += 2
        elif donnees[debut:debut+1] in (b"\n", b"\r"):
            debut += 1

        fin = donnees.find(b"endstream", debut)
        if fin == -1:
            break

        flux = donnees[debut:fin].rstrip(b"\r\n")
        try:
            decompressed = zlib.decompress(flux)
        except zlib.error:
            idx = fin + 9
            continue

        for bt_match in re.finditer(rb'BT(.*?)ET', decompressed, re.DOTALL):
            for tj_match in re.finditer(rb'\((.*?)\)\s*Tj', bt_match.group(1), re.DOTALL):
                morceaux.append(tj_match.group(1).decode('latin-1', errors='replace'))
            for tj_match in re.finditer(rb'\[(.*?)\]\s*TJ', bt_match.group(1), re.DOTALL):
                for str_match in re.finditer(rb'\((.*?)\)', tj_match.group(1), re.DOTALL):
                    morceaux.append(str_match.group(1).decode('latin-1', errors='replace'))

        idx = fin + 9

    return " ".join(morceaux)

def _analyser_degrade(chemin: Path) -> dict[str, Any]:
    donnees = chemin.read_bytes()
    nb_pages = _compter_pages_degrade(donnees)
    if nb_pages == 0:
        return {
            "mode": "degrade (pymupdf absent)",
            "version_pdf": "",
            "nb_pages": 0,
            "nb_images": 0,
            "texte": "",
            "denominateur": 0,
            "examines": [],
            "examines_tronques": False
        }
    nb_images = _compter_images_degrade(donnees)
    texte = _extraire_texte_degrade(donnees)

    examines = []
    for i in range(nb_pages):
        examines.append({
            "page": i + 1,
            "texte": "[indisponible en mode degrade]" if not texte else texte[:100],
            "images": "[indisponible]" if nb_images == 0 else nb_images
        })

    return {
        "mode": "degrade (pymupdf absent)",
        "version_pdf": donnees[:20].split(b'\n')[0].decode('latin-1', errors='ignore'),
        "nb_pages": nb_pages,
        "nb_images": nb_images,
        "texte": texte,
        "denominateur": nb_pages,
        "examines": examines[:200],
        "examines_tronques": nb_pages > 200
    }

def _analyser_pymupdf(chemin: Path) -> dict[str, Any]:
    try:
        doc = pymupdf.open(str(chemin))
    except Exception:
        return {
            "mode": "complet (pymupdf)",
            "version_pdf": "",
            "nb_pages": 0,
            "nb_images": 0,
            "texte": "",
            "denominateur": 0,
            "examines": [],
            "examines_tronques": False
        }

    examines = []
    texte_complet = []
    nb_images_total = 0
    nb_pages = doc.page_count
    version = doc.metadata.get("format", "inconnue") if doc.metadata else "inconnue"

    for i in range(nb_pages):
        page = doc.load_page(i)
        txt = page.get_text("text")
        texte_complet.append(txt)
        images = page.get_images(full=True)
        nb_images_total += len(images)
        examines.append({
            "page": i + 1,
            "texte": txt[:100].strip() + ("..." if len(txt) > 100 else ""),
            "images": len(images)
        })

    doc.close()

    return {
        "mode": "complet (pymupdf)",
        "version_pdf": version,
        "nb_pages": nb_pages,
        "nb_images": nb_images_total,
        "texte": "\n".join(texte_complet),
        "denominateur": nb_pages,
        "examines": examines[:200],
        "examines_tronques": nb_pages > 200
    }

def analyser_pdf(chemin: Path) -> dict[str, Any]:
    if not chemin.exists() or not chemin.is_file():
        return {
            "mode": "erreur",
            "version_pdf": "",
            "nb_pages": 0,
            "nb_images": 0,
            "texte": "",
            "denominateur": 0,
            "examines": [],
            "examines_tronques": False
        }

    if A_PYMUPDF:
        resultat = _analyser_pymupdf(chemin)
    else:
        resultat = _analyser_degrade(chemin)

    return resultat

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extrait le texte, la structure et les images d'un PDF.",
        epilog="Exemple : python extraire_pdf.py document.pdf --json"
    )
    parser.add_argument("cible", type=str, help="Chemin du fichier PDF a analyser")
    parser.add_argument("--json", action="store_true", help="Rend un seul objet JSON sur stdout")
    parser.add_argument("--racine", type=str, help="Surcharge la racine pour la resolution des chemins")

    args = parser.parse_args()

    if args.racine:
        racine = Path(args.racine).resolve()
        if str(racine) not in sys.path:
            sys.path.insert(0, str(racine))
    else:
        racine = Path(__file__).resolve().parent

    cible = Path(args.cible)
    if not cible.is_absolute():
        cible = racine / cible

    resultat = analyser_pdf(cible)

    if resultat["denominateur"] == 0:
        print("Denominateur nul : impossible d'examiner le PDF, refuse de conclure.", file=sys.stderr)
        return 3

    if args.json:
        json.dump({
            "contrat": CONTRAT,
            "denominateur": resultat["denominateur"],
            "examines": resultat["examines"],
            "examines_tronques": resultat["examines_tronques"],
            "mode": resultat["mode"],
            "version_pdf": resultat["version_pdf"],
            "nb_pages": resultat["nb_pages"],
            "nb_images": resultat["nb_images"],
            "texte": resultat["texte"]
        }, sys.stdout, ensure_ascii=False, indent=2)
        return 0

    print(f"Mode : {resultat['mode']}")
    print(f"Version PDF : {resultat['version_pdf']}")
    print(f"Pages : {resultat['nb_pages']}")
    print(f"Images : {resultat['nb_images']}")
    print(f"Texte extrait :\n{resultat['texte'][:1000]}")
    if resultat['examines_tronques']:
        print("(Liste des pages examinees tronquee a 200)")
    for ex in resultat['examines']:
        print(f"Page {ex['page']} : {ex['images']} image(s)")

    if "degrade" in resultat["mode"]:
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())