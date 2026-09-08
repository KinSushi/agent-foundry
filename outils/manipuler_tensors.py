#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""manipuler_tensors.py

QUESTION      Comment transformer ce tenseur ?
MESURE        Nous mesurons le nombre d'éléments du tenseur d'entrée et la liste de leurs valeurs.
HYPOTHÈSES    Le tenseur d'entrée est une liste imbriquée de nombres fournie sous forme de fichier JSON.
LIMITES       Sans les paquets optionnels numpy et/ou einops, l'outil ne peut effectuer que la transformation identité.
CONTRE-EXEMPLES Un fichier d'entrée vide ou ne contenant aucun élément entraîne un refus de conclusion.
INVOCATION
    {outil} {fichier} --pattern "b h -> b h" --json
DOMAINE       Toute transformation linéaire de tenseur pouvant être exprimée par un motif einops.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple, Optional

# ----------------------------------------------------------------------
# Encodage de la console (règle 2)
# ----------------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ----------------------------------------------------------------------
# Imports éventuels de tiers (règle 1)
# ----------------------------------------------------------------------
try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - numpy manquant => mode dégradé
    np = None  # type: ignore

try:
    import einops  # type: ignore
except Exception:  # pragma: no cover - einops manquant => mode dégradé
    einops = None  # type: ignore

# ----------------------------------------------------------------------
# Constantes et chemins
# ----------------------------------------------------------------------
DEFAULT_RACINE = Path(__file__).resolve().parent

__all__ = [
    "charger_tensor",
    "calculer_metrics",
    "transformer_tensor",
    "extraire_contrat",
    "generer_sortie_json",
]

# ----------------------------------------------------------------------
# Cœur de l'outil (sans argparse, sans sys.argv, sans print)
# ----------------------------------------------------------------------
def _apply_racine(racine: Path | None) -> None:
    """Insère la racine en tête de sys.path si fournie."""
    if racine is not None:
        racine_resolue = racine.resolve()
        if str(racine_resolue) not in sys.path:
            sys.path.insert(0, str(racine_resolue))

def generer_sortie_json(
    contrat: Dict[str, str],
    denominateur: int,
    examines: List[Dict[str, Any]],
    examines_tronques: bool,
    resultat: Optional[Any] = None,
    erreur: Optional[str] = None
) -> Dict[str, Any]:
    """Génère le dictionnaire de sortie JSON avec toutes les clés requises."""
    payload = {
        "denominateur": denominateur,
        "examines": examines,
        "examines_tronques": examines_tronques,
        "contrat": contrat
    }
    if erreur is not None:
        payload["erreur"] = erreur
    if resultat is not None:
        payload["resultat"] = resultat
    return payload

def charger_tensor(chemin: Path) -> Any:
    """
    Charge un tenseur depuis un fichier JSON contenant une liste imbriquée.
    Lève FileNotFoundError si le fichier n'existe pas, ValueError si le contenu
    n'est pas une liste JSON valide.
    """
    if not chemin.is_file():
        raise FileNotFoundError(f"Fichier introuvable : {chemin}")
    try:
        contenu = chemin.read_text(encoding="utf-8")
        data = json.loads(contenu)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Contenu JSON invalide : {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("Le JSON fourni doit représenter une liste (éventuellement imbriquée).")
    return data

def _aplatir(obj: Any) -> Iterable[Any]:
    """Parcourt récursivement une liste imbriquée et yields chaque élément scalaire."""
    if isinstance(obj, list):
        for item in obj:
            yield from _aplatir(item)
    else:
        yield obj

def calculer_metrics(tensor: Any) -> Tuple[int, List[Dict[str, Any]], bool]:
    """
    Retourne le dénominateur (nombre total d'éléments), une liste nommée des
    éléments examinés (limitée à 200) et un indicateur de troncature.
    """
    total = 0
    examines: List[Dict[str, Any]] = []
    for idx, valeur in enumerate(_aplatir(tensor)):
        total += 1
        if len(examines) < 200:
            if isinstance(valeur, (int, float)):
                examined_val: Any = int(valeur) if isinstance(valeur, (int,)) and not isinstance(valeur, bool) else float(valeur)
            else:
                examined_val = valeur
            examines.append({"name": f"elem_{idx}", "value": examined_val})
    tronque = total > 200
    return total, examines, tronque

def transformer_tensor(tensor: Any, pattern: str, axes_lengths: Dict[str, int]) -> Any:
    """
    Applique un réarrangement Einops sur le tenseur (nécessite numpy et einops).
    Retourne le tenseur transformé sous forme de liste imbriquée.
    """
    if np is None or einops is None:
        raise RuntimeError("numpy ou einops indisponible")
    arr = np.asarray(tensor)
    transformed = einops.rearrange(arr, pattern, **axes_lengths)
    return transformed.tolist()

def extraire_contrat() -> Dict[str, str]:
    """Extrait les six parties du docstring du module."""
    doc = __doc__ or ""
    lignes = [ligne.strip() for ligne in doc.splitlines() if ligne.strip()]
    sections: Dict[str, str] = {}
    courant: str | None = None
    for ligne in lignes:
        if ligne.endswith(":"):
            courant = ligne.rstrip(":").upper()
            sections[courant] = ""
        elif courant is not None:
            sections[courant] = (sections[courant] + " " + ligne).strip()
    attendu = ["QUESTION", "MESURE", "HYPOTHÈSES", "LIMITES", "CONTRE-EXEMPLES", "DOMAINE"]
    for cle in attendu:
        sections.setdefault(cle, "")
    return sections

def _parser_axes_lengths(paires: List[str]) -> Dict[str, int]:
    """Convertit une liste de 'cle=valeur' en dictionnaire d'entiers."""
    résultat: Dict[str, int] = {}
    for paire in paires:
        if "=" not in paire:
            raise ValueError(f"Argument d'axe mal formé : {paire}")
        cle, valeur = paire.split("=", 1)
        try:
            résultat[cle.strip()] = int(valeur.strip())
        except ValueError as exc:
            raise ValueError(f"Valeur d'axe non entière pour '{cle}' : {valeur}") from exc
    return résultat

def main() -> int:
    """Point d'entrée de l'outil."""
    parser = argparse.ArgumentParser(
        description="Transforme un tenseur selon un motif einops.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Exemple d'appel réel :\n"
            "  manipuler_tensors.py entree.json sortie.json --pattern \"b h w -> b (h w)\"\n"
            "  manipuler_tensors.py entree.json --pattern \"b h w c -> b c h w\" --json"
        ),
    )
    parser.add_argument(
        "entree",
        nargs="?",
        type=Path,
        help="Chemin vers le fichier JSON contenant le tenseur d'entrée (liste imbriquée).",
    )
    parser.add_argument(
        "sortie",
        nargs="?",
        type=Path,
        help="Chemin vers le fichier JSON où écrire le tenseur transformé. "
             "Si omis, le résultat est affiché sur stdout (format JSON).",
    )
    parser.add_argument(
        "--pattern",
        required=True,
        help="Motif einops de réarrangement (ex: \"b h w -> b (h w)\").",
    )
    parser.add_argument(
        "--axes_lengths",
        action="append",
        default=[],
        metavar="CLE=VALEUR",
        help="Longueur d'un axe nommé utilisé dans le motif. Peut être répété.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie au format JSON unique sur stdout (contrat + métriques + résultat).",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        help="Surcharge la racine du projet pour les imports locaux.",
    )

    args = parser.parse_args()
    _apply_racine(args.racine)

    # Vérification des dépendances tierces
    manquants: List[str] = []
    if np is None:
        manquants.append("numpy")
    if einops is None:
        manquants.append("einops")
    if manquants:
        diagnostic = (
            f"Module(s) manquant(s) : {', '.join(manquants)}. "
            "Mode dégradé : l'outil ne peut pas effectuer la transformation einops."
        )
        print(diagnostic, file=sys.stderr)

    # Chargement du tenseur
    try:
        if args.entree is None:
            print("Denominateur nul : aucun fichier d'entrée fourni, refus de conclure.", file=sys.stderr)
            if args.json:
                payload = generer_sortie_json(
                    contrat=extraire_contrat(),
                    denominateur=0,
                    examines=[],
                    examines_tronques=False,
                    erreur="Aucun fichier d'entrée fourni"
                )
                print(json.dumps(payload, ensure_ascii=False))
            return 3

        tenseur = charger_tensor(args.entree)
    except Exception as exc:
        msg = f"Erreur lors du chargement du tenseur : {exc}"
        print(msg, file=sys.stderr)
        if args.json:
            payload = generer_sortie_json(
                contrat=extraire_contrat(),
                denominateur=0,
                examines=[],
                examines_tronques=False,
                erreur=msg
            )
            print(json.dumps(payload, ensure_ascii=False))
        return 2

    # Calcul du dénominateur
    denominateur, examines, examines_tronques = calculer_metrics(tenseur)
    if denominateur == 0:
        print("Denominateur nul : le tenseur ne contient aucun élément, refus de conclure.", file=sys.stderr)
        if args.json:
            payload = generer_sortie_json(
                contrat=extraire_contrat(),
                denominateur=0,
                examines=examines,
                examines_tronques=examines_tronques,
                erreur="Le tenseur ne contient aucun élément"
            )
            print(json.dumps(payload, ensure_ascii=False))
        return 3

    # Parsing des axes lengths
    try:
        axes_lengths = _parser_axes_lengths(args.axes_lengths)
    except ValueError as exc:
        msg = f"Erreur d'argument : {exc}"
        print(msg, file=sys.stderr)
        if args.json:
            payload = generer_sortie_json(
                contrat=extraire_contrat(),
                denominateur=denominateur,
                examines=examines,
                examines_tronques=examines_tronques,
                erreur=msg
            )
            print(json.dumps(payload, ensure_ascii=False))
        return 2

    # Transformation du tenseur
    try:
        if np is None or einops is None:
            tenseur_transforme = tenseur
            print("Attention : transformation identité appliquée (mode dégradé).", file=sys.stderr)
        else:
            tenseur_transforme = transformer_tensor(tenseur, args.pattern, axes_lengths)
    except Exception as exc:
        msg = f"Erreur lors de la transformation : {exc}"
        print(msg, file=sys.stderr)
        if args.json:
            payload = generer_sortie_json(
                contrat=extraire_contrat(),
                denominateur=denominateur,
                examines=examines,
                examines_tronques=examines_tronques,
                erreur=msg
            )
            print(json.dumps(payload, ensure_ascii=False))
        return 2

    # Sortie
    contrat = extraire_contrat()
    if args.json:
        payload = generer_sortie_json(
            contrat=contrat,
            denominateur=denominateur,
            examines=examines,
            examines_tronques=examines_tronques,
            resultat=tenseur_transforme
        )
        print(json.dumps(payload, ensure_ascii=False))
    else:
        resultat_json = json.dumps(tenseur_transforme, ensure_ascii=False)
        if args.sortie is None:
            print(resultat_json)
        else:
            try:
                args.sortie.parent.mkdir(parents=True, exist_ok=True)
                args.sortie.write_text(resultat_json, encoding="utf-8")
            except Exception as exc:
                print(f"Erreur lors de l'écriture du résultat : {exc}", file=sys.stderr)
                return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())