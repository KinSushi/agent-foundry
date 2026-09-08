"""
QUESTION      Ces chaînes sont-elles bien encodées ?
MESURE        Vérification des encodages pour toutes les locales disponibles via les modules locale, gettext et encodings.
HYPOTHESES    Les chaînes doivent être valides dans les encodages déclarés pour chaque locale.
LIMITES       Ne vérifie pas les encodages dynamiques ou non standard. Se base sur les encodages connus de la stdlib.
CONTRE-EXEMPLES Un encodage valide mais non supporté par Python (ex: encodages propriétaires).
INVOCATION
    {outil} {fichier} --json
DOMAINE       Fichiers Python contenant des chaînes littérales ou des fichiers de traduction gettext (.po).
"""

from __future__ import annotations

import argparse
import json
import locale
import sys
from pathlib import Path
from typing import Dict, List, Union, Tuple

__all__ = ["valider_encodages", "analyser_fichier", "main"]

RACINE = Path(__file__).resolve().parent

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

class JsonArgumentParser(argparse.ArgumentParser):
    """ArgumentParser qui renvoie les erreurs sous forme de JSON."""

    def error(self, message: str) -> None:  # pragma: no cover
        json.dump(
            {
                "contrat": {
                    "question": __doc__.split("QUESTION")[1].split("MESURE")[0].strip(),
                    "mesure": __doc__.split("MESURE")[1].split("HYPOTHESES")[0].strip(),
                    "hypotheses": __doc__.split("HYPOTHESES")[1].split("LIMITES")[0].strip(),
                    "limites": __doc__.split("LIMITES")[1].split("CONTRE-EXEMPLES")[0].strip(),
                    "contre_exemples": __doc__.split("CONTRE-EXEMPLES")[1].split("DOMAINE")[0].strip(),
                    "domaine": __doc__.split("DOMAINE")[1].strip(),
                },
                "error": message,
                "denominateur": 0,
            },
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        sys.exit(3)  # Code 3 pour refus légitime

def _locales_disponibles() -> List[Tuple[str, str]]:
    """Retourne la liste des locales disponibles avec leurs encodages."""
    locales: List[Tuple[str, str]] = []
    try:
        for loc, alias in locale.locale_alias.items():
            if "." in alias:
                _, enc = alias.split(".", 1)
                locales.append((loc, enc))
            else:
                try:
                    locale.setlocale(locale.LC_ALL, alias)
                    enc = locale.getpreferredencoding()
                    locales.append((loc, enc))
                except (locale.Error, ValueError):
                    continue
    except Exception:
        pass
    return locales

def valider_encodage(chaine: str, encodage: str) -> bool:
    """Vérifie si une chaîne est valide dans un encodage donné."""
    try:
        chaine.encode(encodage)
        return True
    except UnicodeEncodeError:
        return False

def valider_encodages(chaine: str) -> Dict[str, Union[bool, str]]:
    """Vérifie si une chaîne est valide dans les encodages des locales disponibles."""
    resultats: Dict[str, Union[bool, str]] = {}
    for loc, enc in _locales_disponibles():
        try:
            resultats[f"{loc}.{enc}"] = valider_encodage(chaine, enc)
        except Exception as e:
            resultats[f"{loc}.{enc}"] = f"Erreur: {e}"
    return resultats

def analyser_fichier(chemin: Path) -> Dict[str, Union[int, List[Dict], bool]]:
    """Analyse un fichier Python ou PO pour vérifier les encodages des chaînes."""
    if not chemin.is_file():
        raise FileNotFoundError(f"Fichier introuvable: {chemin}")

    if chemin.suffix == ".py":
        return _analyser_fichier_python(chemin)
    if chemin.suffix == ".po":
        return _analyser_fichier_po(chemin)
    raise ValueError("Format de fichier non supporté (doit être .py ou .po)")

def _analyser_fichier_python(chemin: Path) -> Dict[str, Union[int, List[Dict], bool]]:
    """Analyse un fichier Python pour extraire les chaînes littérales."""
    with chemin.open(encoding="utf-8", errors="strict") as f:
        source = f.read()
    try:
        compile(source, str(chemin), "exec")
    except SyntaxError as e:
        raise ValueError(f"Fichier Python invalide: {e}")

    import ast

    chaines: List[str] = []
    arbre = ast.parse(source, filename=str(chemin))
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Str):
            chaines.append(noeud.s)
        elif isinstance(noeud, ast.Constant) and isinstance(noeud.value, str):
            chaines.append(noeud.value)

    return _creer_resultat(chaines)

def _analyser_fichier_po(chemin: Path) -> Dict[str, Union[int, List[Dict], bool]]:
    """Analyse un fichier PO pour extraire les chaînes traduites."""
    chaines: List[str] = []
    with chemin.open(encoding="utf-8", errors="strict") as f:
        msgid: str | None = None
        for ligne in f:
            if ligne.startswith("msgid "):
                msgid = ligne[6:].strip().strip('"')
            elif ligne.startswith("msgstr ") and msgid:
                msgstr = ligne[7:].strip().strip('"')
                if msgstr:
                    chaines.append(msgstr)
                msgid = None
    return _creer_resultat(chaines)

def _creer_resultat(chaines: List[str]) -> Dict[str, Union[int, List[Dict], bool]]:
    """Crée le résultat d'analyse pour une liste de chaînes."""
    resultats: List[Dict] = []
    for chaine in chaines[:200]:
        validite = valider_encodages(chaine)
        resultats.append(
            {
                "chaine": chaine,
                "validite": validite,
                "tous_valides": all(v is True for v in validite.values()),
            }
        )
    return {
        "denominateur": len(chaines),
        "examines": resultats,
        "examines_tronques": len(chaines) > 200,
    }

def _creer_analyseur() -> argparse.ArgumentParser:
    """Crée l'analyseur d'arguments pour la CLI."""
    parser = JsonArgumentParser(
        description="Vérifie si les chaînes d'un fichier sont bien encodées pour toutes les locales.",
        epilog="Exemple: python validateur_i18n.py mon_fichier.py --json",
    )
    parser.add_argument(
        "fichier",
        type=Path,
        help="Chemin vers le fichier à analyser (.py ou .po)",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=RACINE,
        help="Répertoire racine pour les imports relatifs (défaut: répertoire du script)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie au format JSON sur stdout",
    )
    return parser

def main() -> int:
    """Point d'entrée principal de l'outil."""
    parser = _creer_analyseur()
    args = parser.parse_args()

    if args.racine != RACINE:
        sys.path.insert(0, str(args.racine.resolve()))

    try:
        resultat = analyser_fichier(args.fichier)
    except Exception as e:
        json_error = {
            "contrat": {
                "question": __doc__.split("QUESTION")[1].split("MESURE")[0].strip(),
                "mesure": __doc__.split("MESURE")[1].split("HYPOTHESES")[0].strip(),
                "hypotheses": __doc__.split("HYPOTHESES")[1].split("LIMITES")[0].strip(),
                "limites": __doc__.split("LIMITES")[1].split("CONTRE-EXEMPLES")[0].strip(),
                "contre_exemples": __doc__.split("CONTRE-EXEMPLES")[1].split("DOMAINE")[0].strip(),
                "domaine": __doc__.split("DOMAINE")[1].strip(),
            },
            "error": str(e),
            "denominateur": 0,
        }
        json.dump(json_error, sys.stdout, ensure_ascii=False, indent=2)
        print("Denominateur nul : rien à examiner, refus de conclure.", file=sys.stderr)
        return 3

    denom = resultat["denominateur"]

    if denom == 0:
        if args.json:
            json.dump(
                {
                    "contrat": {
                        "question": __doc__.split("QUESTION")[1].split("MESURE")[0].strip(),
                        "mesure": __doc__.split("MESURE")[1].split("HYPOTHESES")[0].strip(),
                        "hypotheses": __doc__.split("HYPOTHESES")[1].split("LIMITES")[0].strip(),
                        "limites": __doc__.split("LIMITES")[1].split("CONTRE-EXEMPLES")[0].strip(),
                        "contre_exemples": __doc__.split("CONTRE-EXEMPLES")[1].split("DOMAINE")[0].strip(),
                        "domaine": __doc__.split("DOMAINE")[1].strip(),
                    },
                    "denominateur": 0,
                    "examines": [],
                    "examines_tronques": False,
                },
                sys.stdout,
                ensure_ascii=False,
                indent=2,
            )
        print("Denominateur nul : rien à examiner, refus de conclure.", file=sys.stderr)
        return 3

    if args.json:
        json.dump(
            {
                "contrat": {
                    "question": __doc__.split("QUESTION")[1].split("MESURE")[0].strip(),
                    "mesure": __doc__.split("MESURE")[1].split("HYPOTHESES")[0].strip(),
                    "hypotheses": __doc__.split("HYPOTHESES")[1].split("LIMITES")[0].strip(),
                    "limites": __doc__.split("LIMITES")[1].split("CONTRE-EXEMPLES")[0].strip(),
                    "contre_exemples": __doc__.split("CONTRE-EXEMPLES")[1].split("DOMAINE")[0].strip(),
                    "domaine": __doc__.split("DOMAINE")[1].strip(),
                },
                **resultat,
            },
            sys.stdout,
            ensure_ascii=False,
            indent=2,
        )
        return 0

    # Mode texte (humain)
    examines = resultat["examines"]
    invalides = sum(1 for r in examines if not r["tous_valides"])

    if invalides:
        print(
            f"Défauts trouvés: {invalides}/{denom} chaînes ne sont pas valides dans toutes les locales.",
            file=sys.stderr,
        )
        for examen in examines:
            if not examen["tous_valides"]:
                print(f"Chaîne problématique: {examen['chaine']!r}", file=sys.stderr)
                for loc, valide in examen["validite"].items():
                    if valide is not True:
                        print(
                            f"  Locale {loc}: {'✗' if valide is False else valide}",
                            file=sys.stderr,
                        )
        return 1

    print(
        "Aucun défaut trouvé: toutes les chaînes sont valides dans toutes les locales.",
        file=sys.stderr,
    )
    return 0

if __name__ == "__main__":
    raise SystemExit(main())