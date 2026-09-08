"""verifier_docstring.py

QUESTION      cette docstring décrit-elle la fonction qui existe ?
MESURE        docstring_parser pour la structure, inspect.signature et
              get_type_hints pour le réel, croisement nom à nom
HYPOTHÈSES    la docstring suit un style reconnu par docstring_parser
LIMITES       ne vérifie pas que la DESCRIPTION est vraie, seulement la structure ; un type écrit en prose (« un entier positif »)
              n'est pas comparable ; *args et **kwargs échappent au croisement
CONTRE-EXEMPLES type_name is None signifie « non écrit », pas « faux » —
              les confondre accuserait toute docstring sans types
DOMAINE      modules Python importables, docstrings de style reconnu
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import sys
import typing
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

# ------------------------------------------------------------
# Encodage UTF‑8 pour stdout (règle 2)
# ------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# ------------------------------------------------------------
# Gestion de l'import optionnel de docstring_parser (règle 1)
# ------------------------------------------------------------
try:
    from docstring_parser import parse as parse_docstring  # type: ignore
    from docstring_parser.common import DocstringStyle  # type: ignore
except ImportError:  # pragma: no cover
    parse_docstring = None  # type: ignore
    DocstringStyle = None  # type: ignore
    print(
        "Attention : la bibliothèque tierce 'docstring_parser' est introuvable. "
        "L'outil fonctionnera, mais toutes les docstrings seront considérées "
        "comme NON VÉRIFIABLE.",
        file=sys.stderr,
    )

# ------------------------------------------------------------
# Codes de sortie (règle 5, 11)
# ------------------------------------------------------------
EXIT_OK = 0
EXIT_DEFECT = 1
EXIT_DENOMINATEUR_ZERO = 3  # modifié pour respecter la règle du dénominateur zéro

# ------------------------------------------------------------
# Types utiles
# ------------------------------------------------------------
ResultatFonction = Dict[str, Any]

def _charger_module(chemin_module: Path, racine: Path) -> Any:
    """Charge dynamiquement le module indiqué.

    Le fichier est d'abord compilé (règle 10) afin de garantir la validité
    syntaxique avant l'import.
    """
    spec_name = f"_verif_{chemin_module.stem}_{chemin_module.stat().st_ino}"
    spec = importlib.util.spec_from_file_location(spec_name, chemin_module)
    if spec is None or spec.loader is None:
        raise ImportError(f"Impossible de créer le spec pour {chemin_module}")

    # Vérification syntaxique
    source = chemin_module.read_text(encoding="utf-8")
    compile(source, str(chemin_module), "exec")  # raise SyntaxError si invalide

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec_name] = module
    spec.loader.exec_module(module)  # type: ignore[arg-type]
    return module

def _analyser_fonction(func: Callable[..., Any]) -> ResultatFonction:
    """Analyse une fonction et renvoie son état de conformité."""
    nom = func.__name__
    signature = inspect.signature(func)
    hints = typing.get_type_hints(func)

    doc = inspect.getdoc(func)
    if not doc:
        return {"nom": nom, "etat": "NON VÉRIFIABLE", "detail": "absence de docstring", "fonction_objet": func}

    if parse_docstring is None:
        # Remplacement de l'état ERREUR_ANALYSE par NON VÉRIFIABLE
        return {"nom": nom, "etat": "NON VÉRIFIABLE", "detail": "docstring_parser manquant", "fonction_objet": func}

    try:
        doc_struct = parse_docstring(doc)  # type: ignore[call-arg]
    except Exception as exc:  # pragma: no cover
        return {
            "nom": nom,
            "etat": "NON VÉRIFIABLE",
            "detail": f"échec du parsing de la docstring : {exc}",
            "fonction_objet": func,
        }

    # ----------------------------------------------------------------
    # Vérification de la présence de la section Args
    # ----------------------------------------------------------------
    if not getattr(doc_struct, "params", None):
        return {"nom": nom, "etat": "NON VÉRIFIABLE", "detail": "section Args absente", "fonction_objet": func}

    # ----------------------------------------------------------------
    # Extraction des paramètres documentés
    # ----------------------------------------------------------------
    params_doc = {p.arg_name: p.type_name for p in getattr(doc_struct, "params", [])}
    # ----------------------------------------------------------------
    # Extraction des paramètres réels (avec *args/**kwargs)
    # ----------------------------------------------------------------
    params_sig = {}
    hors_croisement = []
    for name, param in signature.parameters.items():
        if param.kind in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY):
            params_sig[name] = param
        else:
            hors_croisement.append(name)

    # ----------------------------------------------------------------
    # Détection des problèmes
    # ----------------------------------------------------------------
    fantomes: List[str] = [n for n in params_doc if n not in params_sig]
    non_documentes: List[str] = [n for n in params_sig if n not in params_doc]

    type_faux: List[Tuple[str, str, str]] = []
    for name in params_sig:
        if name in params_doc:
            doc_type = params_doc[name]
            real_type = hints.get(name)
            # Normalisation des types en chaîne pour comparaison
            real_type_str = (
                getattr(real_type, "__name__", str(real_type)) if real_type else None
            )
            # Ne comparer que si le type réel et le type documenté sont présents
            if real_type_str is not None and doc_type is not None and real_type_str != doc_type:
                type_faux.append((name, doc_type, real_type_str))

    # Retour
    retour_doc_type = getattr(doc_struct.returns, "type_name", None) if getattr(doc_struct, "returns", None) else None
    retour_real_type = hints.get("return")
    retour_real_type_str = (
        getattr(retour_real_type, "__name__", str(retour_real_type))
        if retour_real_type
        else None
    )
    retour_faux = None
    if retour_doc_type is not None and retour_real_type_str is not None and retour_real_type_str != retour_doc_type:
        retour_faux = (retour_doc_type, retour_real_type_str)

    # ----------------------------------------------------------------
    # Construction de l'état
    # ------------------------------------------------------------
    if fantomes:
        return {
            "nom": nom,
            "etat": "FANTÔME",
            "detail": f"paramètres inexistants documentés : {fantomes}",
            "fonction_objet": func,
        }
    if non_documentes:
        return {
            "nom": nom,
            "etat": "NON DOCUMENTÉ",
            "detail": f"paramètres non documentés : {non_documentes}",
            "fonction_objet": func,
        }
    details: List[str] = []
    if type_faux:
        details.extend(f"{n} (doc={dt}, réel={rt})" for n, dt, rt in type_faux)
    if retour_faux:
        dt, rt = retour_faux
        details.append(f"retour (doc={dt}, réel={rt})")
    if details:
        return {
            "nom": nom,
            "etat": "TYPE FAUX",
            "detail": ", ".join(details),
            "fonction_objet": func,
        }

    resultat: ResultatFonction = {"nom": nom, "etat": "CONCORDANT", "detail": ""}
    if hors_croisement:
        resultat["detail"] = f"hors croisement: {hors_croisement}"
        resultat["hors_croisement"] = hors_croisement
    resultat["fonction_objet"] = func
    return resultat

def verifier(chemin_module: str) -> List[ResultatFonction]:
    """Vérifie toutes les fonctions d'un module Python.

    Retourne une liste de dictionnaires contenant le nom de la fonction,
    son état et les détails éventuels.
    """
    racine = Path(__file__).resolve().parent
    module_path = (racine / chemin_module).resolve()
    if not module_path.is_file():
        raise FileNotFoundError(f"Le fichier {module_path} n'existe pas.")
    module = _charger_module(module_path, racine)

    fonctions = [
        obj
        for _, obj in inspect.getmembers(module, inspect.isfunction)
        if obj.__module__ == module.__name__
    ]

    if not fonctions:
        raise ValueError(f"Aucune fonction détectée dans {module_path}")

    resultats = [_analyser_fonction(f) for f in fonctions]
    return resultats

def _detecter_styles(resultats: List[ResultatFonction]) -> Dict[str, int]:
    """Détecte les styles de docstring utilisés dans les résultats."""
    if DocstringStyle is None:
        return {}

    styles: Dict[str, int] = {}
    for r in resultats:
        if r["etat"] == "NON VÉRIFIABLE":
            continue
        doc = inspect.getdoc(r.get("fonction_objet"))
        if not doc:
            continue
        try:
            doc_struct = parse_docstring(doc)  # type: ignore[call-arg]
            style_name = doc_struct.style.name if doc_struct.style else "INCONNU"
            styles[style_name] = styles.get(style_name, 0) + 1
        except Exception:  # pragma: no cover
            continue
    return styles

def _format_human(resultats: List[ResultatFonction]) -> str:
    lignes = []
    for r in resultats:
        ligne = f"{r['nom']}: {r['etat']}"
        if r["detail"]:
            ligne += f" – {r['detail']}"
        if "hors_croisement" in r:
            ligne += f" – hors croisement: {r['hors_croisement']}"
        lignes.append(ligne)
    return "\n".join(lignes)

def _format_json(
    resultats: List[ResultatFonction],
    racine: Path,
    chemin_module: str,
    styles: Dict[str, int] | None,
) -> Dict[str, Any]:
    # On retire la clé non sérialisable "fonction_objet" pour la sortie JSON
    resultats_json = []
    for r in resultats:
        r_copy = r.copy()
        r_copy.pop("fonction_objet", None)
        resultats_json.append(r_copy)

    data = {
        "racine": str(racine),
        "module": chemin_module,
        "denominateur": len(resultats),  # nombre d'éléments réellement examinés
        "resultats": resultats_json,
        "contrat": {
            "QUESTION": "cette docstring décrit-elle la fonction qui existe ?",
            "MESURE": "docstring_parser pour la structure, inspect.signature et get_type_hints pour le réel, croisement nom à nom",
            "HYPOTHÈSES": "la docstring suit un style reconnu par docstring_parser",
            "LIMITES": "ne vérifie pas que la DESCRIPTION est vraie, seulement la structure ; un type écrit en prose (« un entier positif ») n'est pas comparable ; *args et **kwargs échappent au croisement",
            "CONTRE-EXEMPLES": "type_name is None signifie « non écrit », pas « faux » — les confondre accuserait toute docstring sans types",
            "DOMAINE": "modules Python importables, docstrings de style reconnu",
        },
    }
    if styles is not None:
        data["styles"] = styles
    return data

def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Vérifie la conformité des docstrings d'un module Python.",
        epilog="Exemple : python -m outils.verifier_docstring mon_module.py --json",
    )
    parser.add_argument(
        "module",
        help="Chemin (relatif à la racine) du fichier .py à analyser.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Chemin racine du projet (défaut : répertoire contenant cet outil).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Produit une sortie JSON unique sur stdout.",
    )
    args = parser.parse_args()

    racine: Path = args.racine.resolve()
    try:
        resultats = verifier(args.module)
    except Exception as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return EXIT_DEFECT

    if not resultats:
        print("Aucun résultat à afficher (aucune fonction détectée).", file=sys.stderr)
        return EXIT_DENOMINATEUR_ZERO

    styles = _detecter_styles(resultats) if args.json else None

    if args.json:
        sortie = _format_json(resultats, racine, args.module, styles)
        json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        print(_format_human(resultats))

    # Détermination du code de sortie
    if any(r["etat"] not in ("CONCORDANT", "NON VÉRIFIABLE") for r in resultats):
        return EXIT_DEFECT
    return EXIT_OK

if __name__ == "__main__":
    raise SystemExit(main())