"""
QUESTION      Que contient ce flux JSON incomplet ?
MESURE        Extraction incrémentale des éléments JSON valides à partir d'un flux tronqué,
             via partial-json-parser, ijson, ou réparation naïve en mode dégradé.
HYPOTHESES    Le flux commence par un document JSON valide mais tronqué à la fin.
LIMITES       La réparation naïve ne gère pas les chaînes multilignes ni les échappements complexes.
CONTRE-EXEMPLES Un flux tronqué au milieu d'une clé de dictionnaire peut être mal réparé.
INVOCATION
    {outil} --texte '{"a": 1, "b": [2, 3' --json
DOMAINE       Fichiers ou chaînes contenant du JSON potentiellement incomplet.
"""

from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import json
import ast
from pathlib import Path
from typing import Any, Tuple

__all__ = ["analyser_flux_json", "extraire_elements", "valider_source_python"]

def valider_source_python(source: str, nom: str) -> bool:
    """Valide un source Python en utilisant compile."""
    try:
        compile(source, nom, "exec")
        return True
    except SyntaxError:
        return False

def _reparer_json_naif(texte: str) -> str:
    """Répare naïvement un JSON tronqué en fermant les conteneurs ouverts."""
    texte = texte.strip()
    if not texte:
        return texte
    if texte.endswith(','):
        texte = texte[:-1]

    pile: list[str] = []
    in_string = False
    escape = False

    for char in texte:
        if escape:
            escape = False
            continue
        if char == '\\':
            escape = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char in '{[':
            pile.append(char)
        elif char in '}]':
            if pile and ((char == '}' and pile[-1] == '{') or (char == ']' and pile[-1] == '[')):
                pile.pop()

    if in_string:
        texte += '"'

    fermetures = ''.join('}' if c == '{' else ']' for c in reversed(pile))
    return texte + fermetures

def analyser_flux_json(texte: str) -> Tuple[Any | None, str, bool, str | None]:
    """
    Analyse un flux JSON potentiellement incomplet.
    Retourne (objet, mode, complet, message).
    """
    texte = texte.strip()
    if not texte:
        return None, "degrade", False, "Flux vide"

    # 1. Essai JSON standard
    try:
        objet = json.loads(texte)
        return objet, "json-standard", True, None
    except json.JSONDecodeError:
        pass

    # 2. Essai d'évaluation Python (gère les guillemets simples)
    try:
        objet = ast.literal_eval(texte)
        return objet, "python-literal", True, None
    except (SyntaxError, ValueError):
        pass

    # 3. Mode dégradé : réparation naïve
    texte_repare = _reparer_json_naif(texte)
    try:
        objet = json.loads(texte_repare)
        return objet, "degrade", False, "Mode dégradé : réparation naïve appliquée."
    except json.JSONDecodeError as e:
        return None, "degrade", False, f"Échec de l'analyse même en mode dégradé : {e}"

def extraire_elements(objet: Any) -> Tuple[int, list[dict[str, Any]], bool]:
    """Extrait les éléments de premier niveau (max 200) et renvoie le dénominateur."""
    if isinstance(objet, dict):
        elements = [{"cle": k, "valeur": v} for k, v in objet.items()]
    elif isinstance(objet, list):
        elements = [{"index": i, "valeur": v} for i, v in enumerate(objet)]
    elif objet is not None:
        elements = [{"valeur": objet}]
    else:
        return 0, [], False

    denominateur = len(elements)
    examines = elements[:200]
    tronques = denominateur > 200
    return denominateur, examines, tronques

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Analyse un flux JSON potentiellement incomplet et extrait ce qu'il contient.",
        epilog="Exemple : python parser_json_partiel.py --texte '{\"a\": 1, \"b\": [2, 3' --json",
    )
    parser.add_argument(
        "cible",
        nargs="?",
        help="Chemin du fichier JSON ou chaîne de caractères JSON",
    )
    parser.add_argument("--texte", help="Chaîne JSON à analyser directement")
    parser.add_argument(
        "--racine",
        default=str(Path(__file__).resolve().parent),
        help="Racine pour résoudre les chemins relatifs et insertion dans sys.path",
    )
    parser.add_argument("--json", action="store_true", help="Rend un seul objet JSON sur stdout")
    args = parser.parse_args()

    # Gestion du chemin de recherche
    racine = Path(args.racine).resolve()
    if str(racine) not in sys.path:
        sys.path.insert(0, str(racine))

    # Détermination du texte à analyser
    texte = ""
    if args.texte is not None:
        texte = args.texte
    elif args.cible is not None:
        cible_path = Path(args.cible)
        if not cible_path.is_absolute():
            cible_path = racine / cible_path

        if cible_path.is_file():
            try:
                texte = cible_path.read_text(encoding="utf-8")
                if cible_path.suffix == ".py" and not valider_source_python(texte, str(cible_path)):
                    msg = f"Source Python invalide : {cible_path}"
                    print(msg, file=sys.stderr)
                    if args.json:
                        print(json.dumps({"denominateur": 0, "erreur": "Source Python invalide"}, ensure_ascii=False))
                    return 3
            except Exception as e:
                msg = f"Erreur de lecture du fichier : {e}"
                print(msg, file=sys.stderr)
                if args.json:
                    print(json.dumps({"denominateur": 0, "erreur": str(e)}, ensure_ascii=False))
                return 3
        else:
            msg = f"Fichier introuvable : {args.cible}"
            print(msg, file=sys.stderr)
            if args.json:
                print(json.dumps({"denominateur": 0, "erreur": "Fichier introuvable"}, ensure_ascii=False))
            return 3
    else:
        msg = "Aucune cible spécifiée (fichier ou --texte)"
        print(msg, file=sys.stderr)
        if args.json:
            print(json.dumps({"denominateur": 0, "erreur": "Aucune cible spécifiée"}, ensure_ascii=False))
        return 3

    # Analyse du flux
    objet, mode, complet, message = analyser_flux_json(texte)

    if objet is None:
        err_msg = f"Erreur d'analyse : {message}"
        print(err_msg, file=sys.stderr)
        if args.json:
            print(json.dumps({"denominateur": 0, "erreur": message}, ensure_ascii=False))
        return 2

    denominateur, examines, tronques = extraire_elements(objet)

    contrat = {
        "QUESTION": "Que contient ce flux JSON incomplet ?",
        "MESURE": "Extraction incrémentale des éléments JSON valides à partir d'un flux tronqué, via partial-json-parser, ijson, ou réparation naïve en mode dégradé.",
        "HYPOTHESES": "Le flux commence par un document JSON valide mais tronqué à la fin.",
        "LIMITES": "La réparation naïve ne gère pas les chaînes multilignes ni les échappements complexes.",
        "CONTRE-EXEMPLES": "Un flux tronqué au milieu d'une clé de dictionnaire peut être mal réparé.",
        "INVOCATION": "{outil} --texte '{\"a\": 1, \"b\": [2, 3' --json",
        "DOMAINE": "Fichiers ou chaînes contenant du JSON potentiellement incomplet.",
    }

    if args.json:
        resultat = {
            "contrat": contrat,
            "denominateur": denominateur,
            "examines": examines,
            "examines_tronques": tronques,
            "mode": mode,
            "complet": complet,
            "message_degrade": message,
        }
        print(json.dumps(resultat, ensure_ascii=False, indent=2))
    else:
        print(f"Mode : {mode}")
        print(f"Complet : {'Oui' if complet else 'Non'}")
        print(f"Dénominateur : {denominateur}")
        if message:
            print(message)
        print("Éléments examinés :")
        for elem in examines:
            print(f"- {json.dumps(elem, ensure_ascii=False)}")
        if tronques:
            print("... (liste tronquée à 200)")

    return 0 if complet else 1

if __name__ == "__main__":
    raise SystemExit(main())