"""
QUESTION      Quelles variables d'environnement ce processus voit-il ?
MESURE        Enumere les variables de os.environ, charge optionnellement un fichier .env, et valide un source Python.
HYPOTHESES    Le processus herite de l environnement du shell appelant ; un fichier .env peut completer ces variables.
LIMITES       Ne distingue pas l origine (shell vs .env) apres chargement ; ne decrypte aucune valeur.
CONTRE-EXEMPLES Une variable definie dans un .env mais ecrasee par le shell apparait avec la valeur du shell.
DOMAINE       Processus Python 3.14 local, encodage UTF-8 force.
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import os
import argparse
import json
import configparser
import importlib.util
import traceback
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    DOTENV_DISPONIBLE = True
except ImportError:
    DOTENV_DISPONIBLE = False

try:
    from pydantic_settings import BaseSettings
    PYDANTIC_DISPONIBLE = True
except ImportError:
    PYDANTIC_DISPONIBLE = False

__all__ = ["examiner_environnement", "charger_dotenv", "valider_source", "main"]

CONTRAT = {
    "QUESTION": "Quelles variables d'environnement ce processus voit-il ?",
    "MESURE": "Enumere les variables de os.environ, charge optionnellement un fichier .env, et valide un source Python.",
    "HYPOTHESES": "Le processus herite de l environnement du shell appelant ; un fichier .env peut completer ces variables.",
    "LIMITES": "Ne distingue pas l origine (shell vs .env) apres chargement ; ne decrypte aucune valeur.",
    "CONTRE-EXEMPLES": "Une variable definie dans un .env mais ecrasee par le shell apparait avec la valeur du shell.",
    "DOMAINE": "Processus Python 3.14 local, encodage UTF-8 force."
}


def examiner_environnement() -> dict[str, str]:
    """Renvoie une copie de l'environnement actuel du processus."""
    return dict(os.environ)


def charger_dotenv(chemin: Path) -> tuple[bool, str]:
    """Charge un fichier .env dans os.environ. Renvoie (succes, message)."""
    if DOTENV_DISPONIBLE:
        try:
            load_dotenv(chemin)
            return True, ""
        except Exception as e:
            return False, str(e)
    else:
        try:
            texte = chemin.read_text(encoding="utf-8")
            for ligne in texte.splitlines():
                ligne = ligne.strip()
                if not ligne or ligne.startswith("#"):
                    continue
                if "=" not in ligne:
                    continue
                cle, _, val = ligne.partition("=")
                cle = cle.strip()
                if cle.startswith("export "):
                    cle = cle[7:].strip()
                val = val.strip()
                if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                    val = val[1:-1]
                if cle:
                    os.environ[cle] = val
            return True, ""
        except Exception as e:
            return False, str(e)


def valider_source(chemin: Path, racine: Path) -> tuple[bool, list[str], str]:
    """Compile et importe un source Python, valide les BaseSettings trouvees."""
    if str(racine) not in sys.path:
        sys.path.insert(0, str(racine))
    try:
        source = chemin.read_text(encoding="utf-8")
        compile(source, str(chemin), "exec")
    except SyntaxError as e:
        return False, [], f"Erreur de syntaxe: {e}"
    except Exception as e:
        return False, [], f"Erreur de lecture: {e}"

    settings_valides: list[str] = []
    if PYDANTIC_DISPONIBLE:
        try:
            spec = importlib.util.spec_from_file_location(chemin.stem, chemin)
            if spec is None or spec.loader is None:
                return False, [], "Impossible de creer le spec pour le module."
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for nom, obj in vars(module).items():
                if isinstance(obj, type) and issubclass(obj, BaseSettings) and obj is not BaseSettings:
                    try:
                        obj()
                        settings_valides.append(nom)
                    except Exception:
                        tb = traceback.format_exc()
                        return False, [], f"Validation echouee pour {nom}:\n{tb}"
        except Exception:
            tb = traceback.format_exc()
            return False, [], f"Erreur d importation du module:\n{tb}"
    return True, settings_valides, ""


def main() -> int:
    """Point d'entree CLI."""
    parser = argparse.ArgumentParser(
        description="Examine les variables d'environnement visibles par le processus.",
        epilog="Exemple: python manipuler_env.py --env .env --source app.py --json"
    )
    parser.add_argument("--env", type=Path, help="Chemin vers un fichier .env a charger.")
    parser.add_argument("--source", type=Path, help="Fichier source Python a valider.")
    parser.add_argument("--racine", type=Path, default=Path(__file__).resolve().parent, help="Racine pour les imports.")
    parser.add_argument("--json", action="store_true", help="Sortie JSON.")

    args = parser.parse_args()

    messages_stderr: list[str] = []
    defect = False
    settings_valides: list[str] = []

    if args.env:
        if not args.env.exists():
            messages_stderr.append(f"Fichier .env introuvable: {args.env}")
            defect = True
        else:
            ok, err = charger_dotenv(args.env)
            if not ok:
                messages_stderr.append(f"Erreur lors du chargement .env: {err}")
                defect = True
            if not DOTENV_DISPONIBLE:
                messages_stderr.append("Mode degrade: python-dotenv absent, utilisation du parser natif.")

    if args.source:
        if not args.source.exists():
            messages_stderr.append(f"Source introuvable: {args.source}")
            defect = True
        else:
            ok, settings, err = valider_source(args.source, args.racine)
            if not ok:
                messages_stderr.append(err)
                defect = True
            else:
                settings_valides = settings
            if not PYDANTIC_DISPONIBLE:
                messages_stderr.append("Mode degrade: pydantic-settings absent, validation des settings desactivee.")

    env = examiner_environnement()
    denominateur = len(env)

    if denominateur == 0:
        print("Denominateur nul: aucune variable d'environnement visible. Refus de conclure.", file=sys.stderr)
        if args.json:
            print(json.dumps({
                "contrat": CONTRAT,
                "denominateur": 0,
                "examines": [],
                "examines_tronques": False,
                "defect": defect
            }, ensure_ascii=False, indent=2))
        return 3

    examines = [{"nom": k, "valeur": v} for k, v in sorted(env.items())]
    examines_tronques = False
    if len(examines) > 200:
        examines = examines[:200]
        examines_tronques = True

    if args.json:
        sortie = {
            "contrat": CONTRAT,
            "denominateur": denominateur,
            "examines": examines,
            "examines_tronques": examines_tronques,
            "settings_valides": settings_valides,
            "defect": defect
        }
        print(json.dumps(sortie, ensure_ascii=False, indent=2))
    else:
        print(f"Variables d'environnement visibles: {denominateur}")
        for e in examines:
            print(f"  {e['nom']}={e['valeur']}")
        if examines_tronques:
            print("  ... (liste tronquee a 200)")
        if settings_valides:
            print(f"Settings valides: {', '.join(settings_valides)}")

    for msg in messages_stderr:
        print(msg, file=sys.stderr)

    return 1 if defect else 0


if __name__ == "__main__":
    raise SystemExit(main())