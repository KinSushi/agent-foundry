"""
QUESTION      Quel modèle est disponible localement ?
MESURE        Parcourt les répertoires de cache standard (Hugging Face, Transformers) et les répertoires spécifiés par l'utilisateur pour identifier les modèles locaux.
HYPOTHÈSES    Les modèles locaux sont identifiés par la présence de fichiers caractéristiques (config.json, tokenizer.json, pytorch_model.bin, etc.) dans un répertoire.
LIMITES       Ne détecte pas les modèles qui ne suivent pas la structure standard ou qui sont partiellement téléchargés.
CONTRE-EXEMPLES Un répertoire sans fichiers caractéristiques n'est pas considéré comme un modèle.
INVOCATION
    {outil} --json
DOMAINE       Modèles de langage, embeddings, ou artefacts compatibles avec les bibliothèques Hugging Face/Transformers.
"""

from __future__ import annotations
import os
import sys
import json
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

__all__ = ["lister_modeles_locaux", "main"]


def _trouver_chemins_cache() -> List[Path]:
    """Retourne les chemins de cache standard pour Hugging Face et Transformers."""
    chemins: List[Path] = []
    cache_dir = (
        os.getenv("TRANSFORMERS_CACHE")
        or os.getenv("HF_HOME")
        or os.path.expanduser("~/.cache/huggingface")
    )
    chemins.append(Path(cache_dir) / "hub")

    try:
        from huggingface_hub import scan_cache_dir  # type: ignore
        hf_cache = scan_cache_dir().cache_dir
        chemins.append(Path(hf_cache))
    except Exception:
        pass

    try:
        from transformers.utils import TRANSFORMERS_CACHE  # type: ignore
        chemins.append(Path(TRANSFORMERS_CACHE))
    except Exception:
        pass

    return [p for p in chemins if p.exists()]


def _est_modele_valide(repertoire: Path) -> bool:
    """Vérifie si un répertoire contient un modèle valide."""
    fichiers_caracteristiques = {
        "config.json",
        "tokenizer.json",
        "pytorch_model.bin",
        "model.safetensors",
        "tf_model.h5",
    }
    return any((repertoire / f).is_file() for f in fichiers_caracteristiques)


def _contrat() -> Dict[str, str]:
    """Renvoie le dictionnaire de contrat partagé par toutes les sorties."""
    return {
        "QUESTION": "Quel modèle est disponible localement ?",
        "MESURE": "Parcourt les répertoires de cache standard et les répertoires spécifiés pour identifier les modèles locaux.",
        "HYPOTHÈSES": "Les modèles locaux sont identifiés par la présence de fichiers caractéristiques dans un répertoire.",
        "LIMITES": "Ne détecte pas les modèles qui ne suivent pas la structure standard ou qui sont partiellement téléchargés.",
        "CONTRE-EXEMPLES": "Un répertoire sans fichiers caractéristiques n'est pas considéré comme un modèle.",
        "DOMAINE": "Modèles de langage, embeddings, ou artefacts compatibles avec les bibliothèques Hugging Face/Transformers.",
    }


def _construire_sortie(
    denominateur: int,
    examines: Optional[List[Dict[str, Any]]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Construit l'objet JSON unique retourné par l'outil."""
    sortie: Dict[str, Any] = {
        "denominateur": denominateur,
        "examines": examines if examines is not None else [],
        "contrat": _contrat(),
    }
    if error:
        sortie["erreur"] = error
    return sortie


def lister_modeles_locaux(racine: Optional[Path] = None) -> Dict[str, Any]:
    """
    Liste les modèles disponibles localement.

    Args:
        racine: Répertoire racine à scanner. Si None, utilise les caches standard.

    Returns:
        Dictionnaire conforme au contrat, contenant au minimum les clés
        « denominateur », « examines » et « contrat ».
    """
    chemins_a_scanner = _trouver_chemins_cache()
    if racine is not None:
        chemins_a_scanner.append(racine)

    modeles: List[Dict[str, Any]] = []
    for chemin in chemins_a_scanner:
        if not chemin.is_dir():
            continue
        for element in chemin.iterdir():
            if element.is_dir() and _est_modele_valide(element):
                modeles.append(
                    {
                        "nom": element.name,
                        "chemin": str(element.resolve()),
                        "fichiers": [
                            f.name for f in element.iterdir() if f.is_file()
                        ][:5],
                    }
                )

    # déduplication
    uniques: List[Dict[str, Any]] = []
    vus: set[str] = set()
    for m in modeles:
        if m["nom"] not in vus:
            vus.add(m["nom"])
            uniques.append(m)

    tronque = len(uniques) > 200
    examines = uniques[:200] if tronque else uniques

    return _construire_sortie(
        denominateur=len(uniques),
        examines=examines,
        error=None,
    )


def _afficher_resultat_humain(resultat: Dict[str, Any]) -> None:
    """Affiche le résultat en format lisible (stderr)."""
    if resultat["denominateur"] == 0:
        print("Aucun modèle local trouvé.", file=sys.stderr)
        return

    print(
        f"Modèles locaux trouvés ({resultat['denominateur']}):", file=sys.stderr
    )
    for modele in resultat["examines"]:
        print(f"- {modele['nom']} ({modele['chemin']})", file=sys.stderr)
        print(f"  Fichiers: {', '.join(modele['fichiers'])}", file=sys.stderr)
    if resultat.get("examines_tronques"):
        print(
            f"... {resultat['denominateur'] - 200} modèles supplémentaires non affichés.",
            file=sys.stderr,
        )


def main() -> int:
    """Point d'entrée principal de la CLI."""
    parser = argparse.ArgumentParser(
        description="Liste les modèles disponibles localement.",
        epilog="Exemple: python charger_modele.py --racine /mon/dossier --json",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        help="Répertoire racine à scanner pour les modèles. Par défaut, utilise les caches standard.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Affiche le résultat au format JSON sur stdout.",
    )
    args = parser.parse_args()

    # Gestion des erreurs liées à l'argument --racine
    if args.racine:
        if not args.racine.exists():
            sortie = _construire_sortie(
                denominateur=0,
                error=f"Le répertoire spécifié n'existe pas: {args.racine}",
            )
            if args.json:
                json.dump(sortie, sys.stdout, ensure_ascii=False)
            else:
                print(
                    f"Erreur: Le répertoire spécifié n'existe pas: {args.racine}",
                    file=sys.stderr,
                )
            return 1
        if not args.racine.is_dir():
            sortie = _construire_sortie(
                denominateur=0,
                error=f"Le chemin spécifié n'est pas un répertoire: {args.racine}",
            )
            if args.json:
                json.dump(sortie, sys.stdout, ensure_ascii=False)
            else:
                print(
                    f"Erreur: Le chemin spécifié n'est pas un répertoire: {args.racine}",
                    file=sys.stderr,
                )
            return 1

    try:
        resultat = lister_modeles_locaux(args.racine)
    except Exception as exc:  # pragma: no cover – protection contre l'imprévu
        sortie = _construire_sortie(
            denominateur=0, error=f"Erreur lors de la recherche de modèles: {exc}"
        )
        if args.json:
            json.dump(sortie, sys.stdout, ensure_ascii=False)
        else:
            print(f"Erreur lors de la recherche de modèles: {exc}", file=sys.stderr)
        return 2

    # Cas où aucun modèle n'est trouvé → refus légitime (code 3)
    if resultat["denominateur"] == 0:
        # ligne de refus sur stderr
        print(
            "Denominateur nul : rien à examiner, refus de conclure.",
            file=sys.stderr,
        )
        if args.json:
            # on ajoute un message d'erreur pour la lisibilité, mais le champ
            # « denominateur » reste présent et vaut 0.
            resultat = _construire_sortie(
                denominateur=0, error="Aucun modèle local trouvé."
            )
            json.dump(resultat, sys.stdout, ensure_ascii=False)
        return 3

    if args.json:
        json.dump(resultat, sys.stdout, ensure_ascii=False)
        return 0

    _afficher_resultat_humain(resultat)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())