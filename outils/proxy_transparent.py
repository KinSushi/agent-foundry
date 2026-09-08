"""
QUESTION: Puis-je intercepter/modifier des requêtes HTTP ?
MESURE: Analyse statique de fichiers Python pour détecter l'usage de mécanismes d'interception ou modification de requêtes HTTP (proxy, hooks, monkey-patching).
HYPOTHESES: Le code source cible est du Python. Les bibliothèques tierces (httpx, httpcore) sont optionnelles et détectées par motifs textuels.
LIMITES: Un proxy transparent au niveau réseau pur sans modification du code source n'est pas réalisable en Python pur sans droits administrateur et configuration système. L'analyse est statique.
CONTRE-EXEMPLES: Un simple appel httpx.get() sans event_hooks ni transport personnalisé n'intercepte ni ne modifie les requêtes.
INVOCATION
    {outil} {fichier} --json
DOMAINE: Fichiers et répertoires Python.
"""
from __future__ import annotations

import sys
import re
import json
import argparse
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import httpx
    _HTTPX_DISPONIBLE = True
except ImportError:
    _HTTPX_DISPONIBLE = False

try:
    import httpcore
    _HTTPCORE_DISPONIBLE = True
except ImportError:
    _HTTPCORE_DISPONIBLE = False

__all__ = ["analyser_source", "analyser_cible", "formater_json", "formater_humain"]

_MOTIFS: list[tuple[str, str]] = [
    (r"\bevent_hooks\b", "httpx event_hooks"),
    (r"\b(MockTransport|ASGITransport|WSGITransport|HTTPTransport)\b", "httpx transport personnalisé"),
    (r"\bProxyHandler\b", "urllib ProxyHandler"),
    (r"\bHTTP_PROXY\b|\bHTTPS_PROXY\b", "variables d'environnement proxy"),
    (r"\bmonkey_patch\b|\bpatch\b.*\bsocket\b|\bssl\.\w+\s*=\s*|\burllib\.\w+\s*=\s*", "monkey-patching réseau/ssl/urllib"),
]

def analyser_source(chemin: Path) -> dict[str, Any]:
    """Analyse un fichier source Python pour détecter des motifs d'interception HTTP."""
    try:
        code = chemin.read_text(encoding="utf-8")
    except Exception as e:
        return {"chemin": str(chemin), "valide": False, "erreur": str(e), "motifs": []}

    try:
        compile(code, str(chemin), "exec")
    except SyntaxError as e:
        return {"chemin": str(chemin), "valide": False, "erreur": f"SyntaxError: {e}", "motifs": []}
    except Exception as e:
        return {"chemin": str(chemin), "valide": False, "erreur": str(e), "motifs": []}

    motifs_trouves: list[str] = []
    for regex, nom in _MOTIFS:
        if re.search(regex, code):
            motifs_trouves.append(nom)

    return {"chemin": str(chemin), "valide": True, "erreur": None, "motifs": motifs_trouves}

def analyser_cible(cible: Path) -> tuple[int, list[dict[str, Any]], bool]:
    """Analyse tous les fichiers Python d'une cible (fichier ou répertoire)."""
    fichiers: list[Path] = []
    if cible.is_file() and cible.suffix == ".py":
        fichiers = [cible]
    elif cible.is_dir():
        fichiers = sorted(cible.rglob("*.py"))

    denominateur = len(fichiers)
    examines: list[dict[str, Any]] = []
    for f in fichiers:
        res = analyser_source(f)
        examines.append(res)

    tronque = False
    if len(examines) > 200:
        examines = examines[:200]
        tronque = True

    return denominateur, examines, tronque

def _obtenir_contrat() -> dict[str, str]:
    """Retourne le dictionnaire du contrat de l'outil."""
    return {
        "QUESTION": "Puis-je intercepter/modifier des requêtes HTTP ?",
        "MESURE": "Analyse statique de fichiers Python pour détecter l'usage de mécanismes d'interception ou modification de requêtes HTTP.",
        "HYPOTHESES": "Le code source cible est du Python. Les bibliothèques tierces sont optionnelles.",
        "LIMITES": "Un proxy transparent au niveau réseau pur sans modification du code source n'est pas réalisable en Python pur sans droits administrateur.",
        "CONTRE-EXEMPLES": "Un simple appel httpx.get() sans event_hooks ni transport personnalisé n'intercepte ni ne modifie les requêtes.",
        "DOMAINE": "Fichiers et répertoires Python."
    }

def formater_json(denominateur: int, examines: list[dict[str, Any]], tronque: bool) -> str:
    """Formate les résultats en une chaîne JSON."""
    return json.dumps({
        "denominateur": denominateur,
        "contrat": _obtenir_contrat(),
        "examines": examines,
        "examines_tronques": tronque
    }, ensure_ascii=False, indent=2)

def formater_humain(denominateur: int, examines: list[dict[str, Any]], tronque: bool) -> str:
    """Formate les résultats en texte lisible par un humain."""
    lignes: list[str] = []
    lignes.append(f"Fichiers examinés : {denominateur}")
    defaut_trouve = any(e["motifs"] for e in examines)
    if defaut_trouve:
        lignes.append("Interception/modification détectée :")
        for e in examines:
            if e["motifs"]:
                lignes.append(f" - {e['chemin']}: {', '.join(e['motifs'])}")
        if tronque:
            lignes.append("(liste tronquée à 200)")
    else:
        lignes.append("Aucun motif d'interception/modification détecté.")
    return "\n".join(lignes)

def main() -> int:
    """Point d'entrée principal pour la ligne de commande."""
    parser = argparse.ArgumentParser(
        description="Outil d'analyse statique pour détecter l'interception ou modification de requêtes HTTP.",
        epilog="Exemple: python proxy_transparent.py --cible ./mon_projet --json"
    )
    parser.add_argument("--cible", type=Path, required=True, help="Fichier ou répertoire Python à analyser")
    parser.add_argument("--racine", type=Path, default=None, help="Racine du projet (surcharge la racine par défaut)")
    parser.add_argument("--json", action="store_true", help="Rend un seul objet JSON sur stdout")

    args = parser.parse_args()

    racine = args.racine if args.racine else Path(__file__).resolve().parent
    if args.racine:
        sys.path.insert(0, str(args.racine.resolve()))

    cible = args.cible
    if not cible.exists():
        print("Denominateur nul : rien à examiner, refus de conclure.", file=sys.stderr)
        if args.json:
            print(json.dumps({
                "denominateur": 0,
                "contrat": _obtenir_contrat(),
                "erreur": f"La cible {cible} n'existe pas."
            }, ensure_ascii=False))
        return 3
    if not cible.is_file() and not cible.is_dir():
        print("Denominateur nul : rien à examiner, refus de conclure.", file=sys.stderr)
        if args.json:
            print(json.dumps({
                "denominateur": 0,
                "contrat": _obtenir_contrat(),
                "erreur": f"La cible {cible} n'est ni un fichier ni un répertoire."
            }, ensure_ascii=False))
        return 3
    if cible.is_file() and cible.suffix != ".py":
        print("Denominateur nul : rien à examiner, refus de conclure.", file=sys.stderr)
        if args.json:
            print(json.dumps({
                "denominateur": 0,
                "contrat": _obtenir_contrat(),
                "erreur": f"La cible {cible} n'est pas un fichier Python (.py)."
            }, ensure_ascii=False))
        return 3

    denominateur, examines, tronque = analyser_cible(cible)

    if denominateur == 0:
        print("Denominateur nul : rien à examiner, refus de conclure.", file=sys.stderr)
        if args.json:
            print(json.dumps({
                "denominateur": 0,
                "contrat": _obtenir_contrat(),
                "examines": [],
                "examines_tronques": False
            }, ensure_ascii=False))
        return 3

    if not _HTTPX_DISPONIBLE or not _HTTPCORE_DISPONIBLE:
        print("Mode dégradé : httpx ou httpcore non installé. Détection par motifs textuels uniquement.", file=sys.stderr)

    defaut_trouve = any(e["motifs"] for e in examines)

    if args.json:
        print(formater_json(denominateur, examines, tronque))
    else:
        print(formater_humain(denominateur, examines, tronque))

    return 1 if defaut_trouve else 0

if __name__ == "__main__":
    raise SystemExit(main())