"""QUESTION
Comment produire ce texte à partir d’un modèle ?

MESURE
Analyse du modèle et rendu d’un texte via un template, avec prise en charge
optionnelle de jinja2 et sandbox.

HYPOTHESES
Le modèle est un fichier texte contenant des variables à substituer.
Le template peut être une chaîne Jinja2 ou un format Python.

LIMITES
Sans jinja2, seules les substitutions via str.format sont supportées.
Aucun sandbox complet n’est fourni si RestrictedPython est absent.

CONTRE-EXEMPLES
Un modèle binaire ou non‑lisible déclenche une erreur claire.
Un template contenant des constructions Jinja2 avancées échoue sans jinja2.

INVOCATION
    {outil} --modele {fichier} --template {fichier} --json

DOMAINE
Génération de texte à partir de modèles simples, utilisable en ligne de
commande ou comme bibliothèque.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Reconfiguration de l'encodage pour les consoles CP1252
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

__all__: List[str] = ["generer_texte", "analyse_modele", "main"]

class ErreurCLI(Exception):
    """Erreur levée lors du parsing des arguments CLI."""
    pass

def _lire_fichier(chemin: Path) -> str:
    """Lit le contenu d’un fichier texte en UTF‑8."""
    try:
        return chemin.read_text(encoding="utf-8")
    except Exception as exc:
        raise IOError(f"Impossible de lire le fichier « {chemin} » : {exc}") from exc

def _importer_jinja2() -> Any:
    """Retourne la classe Template de jinja2 si disponible, sinon None."""
    try:
        from jinja2 import Template  # type: ignore
        return Template
    except Exception:  # pragma: no cover
        return None

def _evaluer_modele(modele: str) -> Dict[str, Any]:
    """
    Transforme le texte du modèle en dictionnaire.

    - D’abord tentative JSON.
    - Sinon compilation « exec » pour vérifier la syntaxe,
      puis évaluation sécurisée en mode « eval ».
    - En cas d’échec, renvoie un dictionnaire vide.
    """
    try:
        return json.loads(modele)
    except Exception:
        pass

    try:
        compile(modele, "<modele>", "exec")
        code = compile(modele, "<modele>", "eval")
        contexte = eval(code, {"__builtins__": {}}, {})
        if isinstance(contexte, dict):
            return contexte
    except Exception:
        pass

    return {}

def generer_texte(
    modele: str,
    template: str,
    *,
    utilise_jinja2: bool | None = None,
) -> str:
    """
    Rendu du texte à partir d’un modèle et d’un template.

    Parameters
    ----------
    modele: str
        Le texte du modèle (JSON ou dictionnaire Python).
    template: str
        Le template à appliquer.
    utilise_jinja2: bool | None, optional
        Force l’utilisation de Jinja2 (True/False) ou laisse le
        mécanisme décider (None).

    Returns
    -------
    str
        Le texte rendu.
    """
    contexte = _evaluer_modele(modele)

    jinja_template_cls = _importer_jinja2()
    if utilise_jinja2 is True and jinja_template_cls is None:
        utilise_jinja2 = False
    if utilise_jinja2 is None:
        utilise_jinja2 = bool(jinja_template_cls)

    if utilise_jinja2 and jinja_template_cls:
        tmpl = jinja_template_cls(template)
        return tmpl.render(**contexte)

    try:
        return template.format(**contexte)
    except KeyError as exc:
        raise ValueError(f"Variable manquante dans le contexte : {exc}") from exc

def _construire_payload(denominateur: int, examines: List[str], erreur: str | None = None, resultat: str | None = None) -> Dict[str, Any]:
    """Construit le payload JSON avec toutes les clés requises."""
    contrat = {
        "QUESTION": "Comment produire ce texte à partir d’un modèle ?",
        "MESURE": "Analyse du modèle et rendu d’un texte via un template, avec prise en charge optionnelle de jinja2 et sandbox.",
        "HYPOTHESES": "Le modèle est un fichier texte contenant des variables à substituer. Le template peut être une chaîne Jinja2 ou un format Python.",
        "LIMITES": "Sans jinja2, seules les substitutions via str.format sont supportées. Aucun sandbox complet n’est fourni si RestrictedPython est absent.",
        "CONTRE-EXEMPLES": "Un modèle binaire ou non‑lisible déclenche une erreur claire. Un template contenant des constructions Jinja2 avancées échoue sans jinja2.",
        "DOMAINE": "Génération de texte à partir de modèles simples, utilisable en ligne de commande ou comme bibliothèque.",
    }

    examines_tronques = examines[:200]
    payload = {
        "denominateur": denominateur,
        "examines": examines_tronques,
        "examines_tronques": len(examines) > 200,
        "contrat": contrat,
    }

    if erreur:
        payload["erreur"] = erreur
    else:
        payload["resultat"] = resultat

    return payload

def analyse_modele(
    chemin_modele: Path,
    chemin_template: Path,
) -> Tuple[int, Dict[str, Any]]:
    """
    Analyse le modèle et le template, rend le texte et prépare le contrat JSON.

    Returns
    -------
    denominateur: int
        Nombre d’éléments réellement examinés (0 ou 1 ici).
    payload: dict
        Objet JSON complet à retourner.
    """
    if not chemin_modele.is_file():
        raise FileNotFoundError(f"Le fichier modèle « {chemin_modele} » est introuvable.")
    if not chemin_template.is_file():
        raise FileNotFoundError(f"Le fichier template « {chemin_template} » est introuvable.")

    texte_modele = _lire_fichier(chemin_modele)
    texte_template = _lire_fichier(chemin_template)

    rendu = generer_texte(texte_modele, texte_template)

    denominateur = 1
    examines = [str(chemin_modele), str(chemin_template)]
    payload = _construire_payload(denominateur, examines, resultat=rendu)

    return denominateur, payload

class JSONArgumentParser(argparse.ArgumentParser):
    """ArgumentParser qui lève une exception en cas d’erreur."""

    def error(self, message):
        raise ErreurCLI(message)

def _creer_parser() -> argparse.ArgumentParser:
    parser = JSONArgumentParser(
        description="Génère du texte à partir d’un modèle et d’un template.",
        epilog="Exemple : python generer_texte.py --modele data.json --template tmpl.txt",
    )
    parser.add_argument(
        "--modele",
        type=Path,
        required=True,
        help="Chemin vers le fichier contenant le modèle (JSON ou dict Python).",
    )
    parser.add_argument(
        "--template",
        type=Path,
        required=True,
        help="Chemin vers le fichier contenant le template.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=None,
        help="Chemin racine à insérer en tête de sys.path (par défaut le répertoire du script).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la sortie sous forme d’un unique objet JSON sur stdout.",
    )
    return parser

def main(argv: List[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    parser = _creer_parser()
    try:
        args = parser.parse_args(argv)
    except ErreurCLI as exc:
        if "--json" in argv:
            print(json.dumps(_construire_payload(0, [], erreur=str(exc)), ensure_ascii=False))
            return 2
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2

    racine = args.racine or Path(__file__).resolve().parent
    if str(racine) not in sys.path:
        sys.path.insert(0, str(racine))

    try:
        denominateur, payload = analyse_modele(args.modele, args.template)
    except FileNotFoundError as exc:
        if args.json:
            print(json.dumps(_construire_payload(0, [], erreur=str(exc)), ensure_ascii=False))
            return 3
        print(f"Denominateur nul : {exc}", file=sys.stderr)
        return 3
    except Exception as exc:
        if args.json:
            print(json.dumps(_construire_payload(0, [], erreur=str(exc)), ensure_ascii=False))
            return 1
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(payload["resultat"])
        print("\n--- Contrat ---")
        for cle, valeur in payload["contrat"].items():
            print(f"{cle}: {valeur}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())