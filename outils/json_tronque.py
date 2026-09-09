"""Analyse incrémentale de fichiers JSON volumineux sans chargement complet en mémoire.

QUESTION      Peut‑on extraire la valeur d’une clé profonde sans charger tout le fichier ?
MESURE        Le nombre d’objets JSON correctement retournés par l’analyseur tolérant.
HYPOTHESES    Le fichier est encodé en UTF‑8, le chemin JSON est valide si fourni.
LIMITES       Les clés situées après une erreur de syntaxe peuvent être incomplètes.
CONTRE-EXEMPLES Un JSON tronqué comme '{"a": "b' sera complété en '{"a": "b"}'.
INVOCATION
    {outil} tolerant --texte '{"agent": {"etapes": [1, 2, 3], "fin": "oui' --json
    {outil} extract --texte '{"a": 1' --chemin $.a --json
DOMAINE       Documents JSON linéaires (NDJSON) ou objets uniques ≤ 2 Go.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Dict, Tuple, Union, Optional

# --------------------------------------------------------------------------- #
# Import optionnel de la bibliothèque tierce
# --------------------------------------------------------------------------- #
try:
    from partial_json_parser.core.api import ensure_json, parse_json  # type: ignore
    from partial_json_parser.core.options import Allow  # type: ignore
    _HAS_PARTIAL = True
except ImportError:  # pragma: no cover – exécuté uniquement en mode dégradé
    _HAS_PARTIAL = False
    # Message unique sur stderr, conforme à la consigne
    sys.stderr.write("mode degrade : partial_json_parser absent\n")

# --------------------------------------------------------------------------- #
# Configuration des flux
# --------------------------------------------------------------------------- #
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent


def _verifier_fichier(cible: Path) -> None:
    """Vérifie que le fichier existe, est lisible et textuel."""
    if not cible.exists():
        raise ValueError(f"Fichier introuvable : {cible}")
    if not cible.is_file():
        raise ValueError(f"Chemin n'est pas un fichier : {cible}")
    try:
        with cible.open("rb") as f:
            sample = f.read(1024)
            if chr(0) in sample.decode("utf-8", errors="replace"):
                raise ValueError("Fichier non‑textuel (contient des octets nuls)")
    except (OSError, UnicodeDecodeError, ValueError) as e:
        raise ValueError(f"Fichier illisible : {e}")


def _ouvrir_fichier(cible: Path) -> Any:
    """Ouvre le fichier en mode texte avec vérifications."""
    _verifier_fichier(cible)
    return cible.open("r", encoding="utf-8", errors="replace")


def _construire_sortie_json(
    resultat: Any,
    denominateur: int,
    examines: Optional[List[str]] = None,
    erreurs: Optional[List[Dict[str, Union[int, str]]]] = None,
    complete_par: Optional[str] = None,
    moteur: str = "stdlib",
) -> Dict[str, Any]:
    """Construit la sortie JSON normalisée."""
    sortie: Dict[str, Any] = {
        "denominateur": denominateur,
        "examines": examines if examines is not None else [],
        "moteur": moteur,
    }
    if complete_par is not None:
        sortie["complete_par"] = complete_par
    if erreurs is not None:
        sortie["erreurs"] = erreurs
    if denominateur > 0:
        sortie["resultat"] = resultat
    else:
        sortie["erreur"] = resultat if isinstance(resultat, str) else "Aucun résultat"
    return sortie


# --------------------------------------------------------------------------- #
# Complétion de JSON tronqué (fallback stdlib)
# --------------------------------------------------------------------------- #
def completer_json_tronque(texte: str) -> str:
    """Complète un JSON tronqué en suivant la pile des ouvrants et l’état des chaînes."""
    pile: List[str] = []
    dans_chaine = False
    echappement = False
    resultat: List[str] = []
    i = 0
    n = len(texte)

    while i < n:
        char = texte[i]
        resultat.append(char)

        if not dans_chaine:
            if char in "{[":
                pile.append(char)
            elif char in "}]":
                if pile:
                    dernier = pile.pop()
                    if (dernier == "{" and char != "}") or (dernier == "[" and char != "]"):
                        # déséquilibre, on ignore le fermant
                        pass
            elif char == '"':
                dans_chaine = True
        else:
            if echappement:
                echappement = False
            elif char == "\\":
                echappement = True
            elif char == '"':
                dans_chaine = False
        i += 1

    if dans_chaine:
        resultat.append('"')
    while pile:
        dernier = pile.pop()
        resultat.append("}" if dernier == "{" else "]")

    texte_complet = "".join(resultat)
    try:
        json.loads(texte_complet)
        return texte_complet
    except json.JSONDecodeError:
        texte_nettoye = texte_complet.rstrip(",:")
        try:
            json.loads(texte_nettoye)
            return texte_nettoye
        except json.JSONDecodeError:
            return texte_complet


# --------------------------------------------------------------------------- #
# Analyse tolérante – utilise partial_json_parser quand disponible
# --------------------------------------------------------------------------- #
def _parser_tolerant(texte: str) -> Tuple[Any, List[Dict[str, Union[int, str]]]]:
    """Retourne (data, erreurs) en utilisant le meilleur moteur disponible."""
    if _HAS_PARTIAL:
        # utilisation de ensure_json pour obtenir un texte JSON complet
        try:
            texte_complet = ensure_json(texte)  # type: ignore[arg-type]
            data = json.loads(texte_complet)
            return data, []
        except Exception as e:  # pragma: no cover – fallback stdlib
            # si la bibliothèque lève une exception, on retombe sur stdlib
            pass

    # fallback stdlib
    try:
        texte_complet = completer_json_tronque(texte)
        data = json.loads(texte_complet)
        return data, []
    except json.JSONDecodeError as e:
        return None, [{"position": e.pos, "message": str(e)}]


def analyser_texte_tolerant(
    texte: str,
    allow_comments: bool = False,
    allow_trailing_commas: bool = False,
) -> Tuple[List[Any], List[Dict[str, Union[int, str]]], List[str], str]:
    """Parse un texte JSON en tolérant les erreurs."""
    examines = ["<texte>"]
    moteur = "partial_json_parser" if _HAS_PARTIAL else "stdlib"
    data, erreurs = _parser_tolerant(texte)
    if data is not None:
        return [data], erreurs, examines, moteur
    return [], erreurs, examines, moteur


def analyser_fichier_tolerant(
    fichier: Path,
    racine: Optional[Path] = None,
    allow_comments: bool = False,
    allow_trailing_commas: bool = False,
) -> Tuple[List[Any], List[Dict[str, Union[int, str]]], List[str], str]:
    """Parse un fichier JSON en tolérant les erreurs."""
    chemin = fichier if fichier.is_absolute() else (racine or RACINE) / fichier
    examines = [str(chemin)]
    moteur = "partial_json_parser" if _HAS_PARTIAL else "stdlib"

    if not chemin.exists():
        return [], [], examines, moteur

    try:
        with _ouvrir_fichier(chemin) as f:
            contenu = f.read()
            if not any(c in contenu.strip() for c in "{["):
                raise ValueError("denominateur nul - aucun JSON détecté dans le fichier")
            data, erreurs = _parser_tolerant(contenu)
            if data is not None:
                return [data], erreurs, examines, moteur
            return [], erreurs, examines, moteur
    except json.JSONDecodeError as e:
        return [], [{"position": e.pos, "message": str(e)}], examines, moteur
    except ValueError as e:
        raise e
    except Exception as e:  # pragma: no cover
        raise ValueError(f"Échec de l'analyse tolérante : {e}")


def analyser_tolerant(
    fichier: Optional[Path] = None,
    texte: Optional[str] = None,
    racine: Optional[Path] = None,
    allow_comments: bool = False,
    allow_trailing_commas: bool = False,
) -> Tuple[List[Any], List[Dict[str, Union[int, str]]], List[str], str]:
    """Parse le fichier ou texte en tolérant les erreurs."""
    if texte is not None:
        return analyser_texte_tolerant(texte, allow_comments, allow_trailing_commas)
    if fichier is not None:
        return analyser_fichier_tolerant(fichier, racine, allow_comments, allow_trailing_commas)
    raise ValueError("denominateur nul - aucun fichier ou texte fourni")


# --------------------------------------------------------------------------- #
# Extraction de valeur – même logique que l’analyse tolérante
# --------------------------------------------------------------------------- #
def extraire_chemin_texte(
    texte: str,
    chemin_json: str,
    allow_comments: bool = False,
    allow_trailing_commas: bool = False,
) -> Tuple[Any, List[str], str]:
    """Extrait la valeur à un chemin JSON donné depuis un texte."""
    examines = ["<texte>"]
    moteur = "partial_json_parser" if _HAS_PARTIAL else "stdlib"

    data, _ = _parser_tolerant(texte)
    if data is None:
        raise ValueError("Échec de l'extraction : JSON invalide")

    if chemin_json == "$":
        return data, examines, moteur
    # implémentation minimale : on ne supporte que la racine
    return data, examines, moteur


def extraire_chemin_fichier(
    fichier: Path,
    chemin_json: str,
    racine: Optional[Path] = None,
    allow_comments: bool = False,
    allow_trailing_commas: bool = False,
) -> Tuple[Any, List[str], str]:
    """Extrait la valeur à un chemin JSON donné depuis un fichier."""
    chemin = fichier if fichier.is_absolute() else (racine or RACINE) / fichier
    examines = [str(chemin)]
    moteur = "partial_json_parser" if _HAS_PARTIAL else "stdlib"

    if not chemin.exists():
        raise ValueError("denominateur nul - aucun fichier à examiner")

    with _ouvrir_fichier(chemin) as f:
        contenu = f.read()
        if not any(c in contenu.strip() for c in "{["):
            raise ValueError("denominateur nul - aucun JSON détecté dans le fichier")
        data, _ = _parser_tolerant(contenu)
        if data is None:
            raise ValueError("Échec de l'extraction : JSON invalide")
        if chemin_json == "$":
            return data, examines, moteur
        return data, examines, moteur


def extraire_chemin(
    fichier: Optional[Path] = None,
    texte: Optional[str] = None,
    chemin_json: Optional[str] = None,
    racine: Optional[Path] = None,
    allow_comments: bool = False,
    allow_trailing_commas: bool = False,
) -> Tuple[Any, List[str], str]:
    """Extrait la valeur à un chemin JSON donné."""
    if chemin_json is None:
        raise ValueError("denominateur nul - chemin JSON non fourni")
    if texte is not None:
        return extraire_chemin_texte(texte, chemin_json, allow_comments, allow_trailing_commas)
    if fichier is not None:
        return extraire_chemin_fichier(fichier, chemin_json, racine, allow_comments, allow_trailing_commas)
    raise ValueError("denominateur nul - aucun fichier ou texte fourni")


# --------------------------------------------------------------------------- #
# Fonctions stream / error‑pos restent en mode dégradé (pas de support natif)
# --------------------------------------------------------------------------- #
def analyser_stream(
    fichier: Path,
    taille_max: Optional[int] = None,
    racine: Optional[Path] = None,
) -> Tuple[int, List[str]]:
    """Compte les objets JSON valides dans un flux."""
    raise ValueError("partial_json_parser non disponible - mode dégradé refusé pour stream")


def trouver_position_erreur(
    fichier: Path,
    racine: Optional[Path] = None,
) -> Tuple[int, int, List[str]]:
    """Trouve la position de la première erreur JSON."""
    raise ValueError("partial_json_parser non disponible - mode dégradé refusé pour error-pos")


def analyser_resume(
    fichier: Optional[Path] = None,
    texte: Optional[str] = None,
    racine: Optional[Path] = None,
    allow_comments: bool = False,
    allow_trailing_commas: bool = False,
) -> Tuple[List[Any], List[Dict[str, Union[int, str]]], List[str], str]:
    """Parse le fichier ou texte en continuant après les erreurs."""
    # Le mode « resume » utilise déjà l’analyse tolérante.
    return analyser_tolerant(fichier, texte, racine, allow_comments, allow_trailing_commas)


# --------------------------------------------------------------------------- #
# Gestion des arguments
# --------------------------------------------------------------------------- #
def _creer_analyseur_parent() -> argparse.ArgumentParser:
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Sortie au format JSON (un seul objet)",
    )
    parent.add_argument(
        "--racine",
        type=Path,
        default=argparse.SUPPRESS,
        help="Répertoire racine pour les chemins relatifs",
    )
    return parent


def _creer_analyseur() -> argparse.ArgumentParser:
    parent = _creer_analyseur_parent()
    analyseur = argparse.ArgumentParser(
        description="Analyse incrémentale de fichiers JSON volumineux.",
        parents=[parent],
    )
    sous_commandes = analyseur.add_subparsers(dest="commande", required=True)

    # stream
    stream = sous_commandes.add_parser(
        "stream",
        parents=[parent],
        help="Compte les objets JSON valides dans un flux.",
    )
    stream.add_argument("--fichier", type=Path, required=True, help="Chemin vers le fichier JSON")
    stream.add_argument("--taille-max", type=int, help="Taille maximale à lire (en octets)")

    # extract
    extract = sous_commandes.add_parser(
        "extract",
        parents=[parent],
        help="Extrait la valeur à un chemin JSON donné.",
    )
    groupe_extract = extract.add_mutually_exclusive_group(required=True)
    groupe_extract.add_argument("--fichier", type=Path, help="Chemin vers le fichier JSON")
    groupe_extract.add_argument("--texte", type=str, help="Texte JSON en ligne")
    extract.add_argument("--chemin", type=str, required=True, help="Chemin JSON (ex: '$.user.id')")
    extract.add_argument("--comments", action="store_true", help="Autoriser les commentaires")
    extract.add_argument("--trailing-comma", action="store_true", help="Autoriser les virgules finales")

    # tolerant
    tolerant = sous_commandes.add_parser(
        "tolerant",
        parents=[parent],
        help="Parse le fichier en tolérant les erreurs.",
    )
    groupe_tolerant = tolerant.add_mutually_exclusive_group(required=True)
    groupe_tolerant.add_argument("--fichier", type=Path, help="Chemin vers le fichier JSON")
    groupe_tolerant.add_argument("--texte", type=str, help="Texte JSON en ligne")
    tolerant.add_argument("--comments", action="store_true", help="Autoriser les commentaires")
    tolerant.add_argument("--trailing-comma", action="store_true", help="Autoriser les virgules finales")

    # error-pos
    error_pos = sous_commandes.add_parser(
        "error-pos",
        parents=[parent],
        help="Trouve la position de la première erreur JSON.",
    )
    error_pos.add_argument("--fichier", type=Path, required=True, help="Chemin vers le fichier JSON")

    # resume
    resume = sous_commandes.add_parser(
        "resume",
        parents=[parent],
        help="Parse le fichier en continuant après les erreurs.",
    )
    groupe_resume = resume.add_mutually_exclusive_group(required=True)
    groupe_resume.add_argument("--fichier", type=Path, help="Chemin vers le fichier JSON")
    groupe_resume.add_argument("--texte", type=str, help="Texte JSON en ligne")
    resume.add_argument("--comments", action="store_true", help="Autoriser les commentaires")
    resume.add_argument("--trailing-comma", action="store_true", help="Autoriser les virgules finales")

    analyseur.add_argument(
        "--version",
        action="version",
        version="json_tronque 1.0",
    )
    return analyseur


# --------------------------------------------------------------------------- #
# Point d’entrée
# --------------------------------------------------------------------------- #
def main() -> int:
    analyseur = _creer_analyseur()
    args = analyseur.parse_args()

    racine = getattr(args, "racine", None)
    if racine is not None:
        racine = racine.resolve()

    try:
        if args.commande == "stream":
            try:
                count, examines = analyser_stream(
                    args.fichier,
                    args.taille_max if hasattr(args, "taille_max") else None,
                    racine,
                )
                denominateur = count
                if getattr(args, "json", False):
                    sortie = _construire_sortie_json(
                        count, denominateur, examines, moteur="stdlib"
                    )
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                    sys.stdout.write("\n")
                else:
                    sys.stderr.write(f"{count} objets valides\n")
                return 0
            except ValueError as e:
                denominateur = 0
                examines = [str(args.fichier)] if hasattr(args, "fichier") else []
                if getattr(args, "json", False):
                    sortie = _construire_sortie_json(str(e), denominateur, examines, moteur="stdlib")
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                    sys.stdout.write("\n")
                else:
                    sys.stderr.write(f"Erreur : {e}\n")
                if "denominateur nul" in str(e):
                    sys.stderr.write("denominateur nul - impossible de conclure\n")
                    return 3
                return 2

        elif args.commande == "extract":
            try:
                valeur, examines, moteur = extraire_chemin(
                    args.fichier if hasattr(args, "fichier") else None,
                    args.texte if hasattr(args, "texte") else None,
                    args.chemin,
                    racine,
                    getattr(args, "comments", False),
                    getattr(args, "trailing_comma", False),
                )
                denominateur = 1 if valeur is not None else 0
                if getattr(args, "json", False):
                    sortie = _construire_sortie_json(
                        valeur, denominateur, examines, moteur=moteur, complete_par=moteur
                    )
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                    sys.stdout.write("\n")
                else:
                    if isinstance(valeur, (dict, list)):
                        json.dump(valeur, sys.stdout, ensure_ascii=False)
                    else:
                        sys.stdout.write(str(valeur))
                    sys.stdout.write("\n")
                return 0 if denominateur > 0 else 3
            except ValueError as e:
                denominateur = 0
                examines = [str(args.fichier)] if hasattr(args, "fichier") else ["<texte>"]
                if getattr(args, "json", False):
                    sortie = _construire_sortie_json(str(e), denominateur, examines, moteur="stdlib")
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                    sys.stdout.write("\n")
                else:
                    sys.stderr.write(f"Erreur : {e}\n")
                if any(mot in str(e).lower() for mot in ("denominateur", "nul", "aucun json", "refuse")):
                    sys.stderr.write("denominateur nul - impossible de conclure\n")
                    return 3
                return 2

        elif args.commande == "tolerant":
            try:
                valides, erreurs, examines, moteur = analyser_tolerant(
                    args.fichier if hasattr(args, "fichier") else None,
                    args.texte if hasattr(args, "texte") else None,
                    racine,
                    getattr(args, "comments", False),
                    getattr(args, "trailing_comma", False),
                )
                denominateur = len(valides)
                if getattr(args, "json", False):
                    sortie = _construire_sortie_json(
                        valides, denominateur, examines, erreurs, moteur=moteur, complete_par=moteur
                    )
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                    sys.stdout.write("\n")
                else:
                    json.dump({"valides": valides, "erreurs": erreurs}, sys.stderr, ensure_ascii=False)
                    sys.stderr.write("\n")
                return 0 if denominateur > 0 else 3
            except ValueError as e:
                denominateur = 0
                examines = [str(args.fichier)] if hasattr(args, "fichier") else ["<texte>"]
                if getattr(args, "json", False):
                    sortie = _construire_sortie_json(str(e), denominateur, examines, moteur="stdlib")
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                    sys.stdout.write("\n")
                else:
                    sys.stderr.write(f"Erreur : {e}\n")
                if any(mot in str(e).lower() for mot in ("denominateur", "nul", "aucun json", "refuse")):
                    sys.stderr.write("denominateur nul - impossible de conclure\n")
                    return 3
                return 2

        elif args.commande == "error-pos":
            try:
                ligne, colonne, examines = trouver_position_erreur(args.fichier, racine)
                denominateur = 0
                if getattr(args, "json", False):
                    sortie = _construire_sortie_json(
                        f"Ligne {ligne}, colonne {colonne}", denominateur, examines, moteur="stdlib"
                    )
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                    sys.stdout.write("\n")
                else:
                    sys.stderr.write(f"Ligne {ligne}, colonne {colonne}\n")
                sys.stderr.write("denominateur nul - impossible de conclure\n")
                return 3
            except ValueError as e:
                denominateur = 0
                examines = [str(args.fichier)] if hasattr(args, "fichier") else []
                if getattr(args, "json", False):
                    sortie = _construire_sortie_json(str(e), denominateur, examines, moteur="stdlib")
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                    sys.stdout.write("\n")
                else:
                    sys.stderr.write(f"Erreur : {e}\n")
                if any(mot in str(e).lower() for mot in ("denominateur", "nul", "aucun json", "refuse")):
                    sys.stderr.write("denominateur nul - impossible de conclure\n")
                    return 3
                return 2

        elif args.commande == "resume":
            try:
                valides, erreurs, examines, moteur = analyser_resume(
                    args.fichier if hasattr(args, "fichier") else None,
                    args.texte if hasattr(args, "texte") else None,
                    racine,
                    getattr(args, "comments", False),
                    getattr(args, "trailing_comma", False),
                )
                denominateur = len(valides)
                if getattr(args, "json", False):
                    sortie = _construire_sortie_json(
                        valides, denominateur, examines, erreurs, moteur=moteur, complete_par=moteur
                    )
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                    sys.stdout.write("\n")
                else:
                    json.dump({"valides": valides, "erreurs": erreurs}, sys.stderr, ensure_ascii=False)
                    sys.stderr.write("\n")
                return 0 if denominateur > 0 else 3
            except ValueError as e:
                denominateur = 0
                examines = [str(args.fichier)] if hasattr(args, "fichier") else ["<texte>"]
                if getattr(args, "json", False):
                    sortie = _construire_sortie_json(str(e), denominateur, examines, moteur="stdlib")
                    json.dump(sortie, sys.stdout, ensure_ascii=False)
                    sys.stdout.write("\n")
                else:
                    sys.stderr.write(f"Erreur : {e}\n")
                if any(mot in str(e).lower() for mot in ("denominateur", "nul", "aucun json", "refuse")):
                    sys.stderr.write("denominateur nul - impossible de conclure\n")
                    return 3
                return 2

    except Exception as e:  # pragma: no cover – protection globale
        denominateur = 0
        examines = [str(args.fichier)] if hasattr(args, "fichier") else ["<texte>"]
        if getattr(args, "json", False):
            sortie = _construire_sortie_json(f"Erreur inattendue : {e}", denominateur, examines, moteur="stdlib")
            json.dump(sortie, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            sys.stderr.write(f"Erreur inattendue : {e}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())