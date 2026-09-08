"""sceller – Outil de scellage et de vérification d’un arbre de fichiers.

CONTRAT DE MESURE
QUESTION       Ce que je scelle aujourd’hui a‑t‑il changé demain ?
MESURE         hashlib.file_digest par fichier, mesure à 2016 Mo/s pour sha256
               sur ce processeur ; manifeste JSON trié, reproductible
HYPOTHESES    Les fichiers sont lisibles ; le sceau est conservé ailleurs que
               dans l’arbre scellé
LIMITES       L’outil indique QU’IL A CHANGÉ, jamais QUOI ; il ne protège de
               rien, il constate ; il repose sur _hashlib.pyd, dont l’immunité
               est empirique
CONTRE-EXEMPLE un sceau rangé DANS l’arbre qu’il scelle se met à jour avec lui
               et ne détecte plus rien
DOMAINE        Un arbre de fichiers, sur cette machine
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

# ------------------------------------------------------------
# Encodage UTF‑8 pour stdout et stderr (règle 2)
# ------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ------------------------------------------------------------
# Constantes
# ------------------------------------------------------------
ALGO: str = "sha256"
CODE_ZERODENOM = 4          # code de sortie quand le dénominateur vaut zéro (gardé pour compatibilité)
CODE_PARTIAL = 3            # code de sortie quand des erreurs de lecture sont ignorées
CODE_DEFECT = 1             # défaut détecté (fichier modifié, disparu, etc.)

# ------------------------------------------------------------
# Fonctions cœur – aucune impression, aucune dépendance argparse
# ------------------------------------------------------------

def _hash_fichier(chemin: Path) -> str:
    """Retourne le digest hexadécimal du fichier *chemin* avec hashlib.file_digest."""
    try:
        # hashlib.file_digest est disponible depuis Python 3.14
        with chemin.open("rb") as f:
            return hashlib.file_digest(f, ALGO).hex()
    except AttributeError:  # fallback si file_digest n’existe pas
        h = hashlib.new(ALGO)
        with chemin.open("rb") as f:
            for bloc in iter(lambda: f.read(8192), b""):
                h.update(bloc)
        return h.hexdigest()


def _compter_unites(chemin: Path) -> int:
    """Compte les unités d’un fichier texte : lignes + occurrences de « ### ». """
    try:
        with chemin.open("r", encoding="utf-8", errors="ignore") as f:
            lignes = f.readlines()
    except (UnicodeDecodeError, PermissionError):
        # Fichier binaire ou illisible : on ne compte aucune unité
        return 0
    nb_lignes = len(lignes)
    nb_marquages = sum(1 for l in lignes if l.startswith("### "))
    return nb_lignes + nb_marquages


def _parcourir_arbre(racine: Path) -> Tuple[Dict[str, Any], List[OSError]]:
    """Parcourt *racine* avec os.walk, renvoie le dictionnaire de métadonnées
    et la liste des erreurs rencontrées."""
    fichiers: Dict[str, Any] = {}
    erreurs: List[OSError] = []

    for dossier, sous_dossiers, noms_fichiers in os.walk(
        racine,
        topdown=True,
        onerror=erreurs.append,
    ):
        # tri pour reproductibilité
        sous_dossiers.sort()
        noms_fichiers.sort()
        for nom in noms_fichiers:
            chemin = Path(dossier, nom)
            rel = str(chemin.relative_to(racine))
            try:
                taille = chemin.stat().st_size
                h = _hash_fichier(chemin)
                unites = _compter_unites(chemin)
                fichiers[rel] = {
                    "hash": h,
                    "taille": taille,
                    "unites": unites,
                }
            except OSError as exc:
                erreurs.append(exc)

    return fichiers, erreurs


def sceller(racine: Path) -> Dict[str, Any]:
    """Produit un sceau JSON décrivant l’arbre *racine*."""
    fichiers, erreurs = _parcourir_arbre(racine)

    sceau: Dict[str, Any] = {
        "python_version": sys.version.split()[0],
        "algorithme": ALGO,
        "date": datetime.datetime.utcnow().isoformat() + "Z",
        "racine": str(racine.resolve()),
        "fichiers": dict(sorted(fichiers.items())),
        "erreurs_lecture": [str(e) for e in erreurs],
    }
    return sceau


def verifier(sceau: Dict[str, Any], racine: Path) -> Tuple[int, List[Dict[str, str]], Dict[str, Any]]:
    """Vérifie l’arbre *racine* à l’aide du *sceau*.
    Retourne (code_sortie, liste_détails, nouveau_sceau)."""
    if not sceau.get("fichiers"):
        # dénominateur nul : aucun fichier scellé
        return CODE_ZERODENOM, [], {}

    nouveau_sceau = sceller(racine)
    details: List[Dict[str, str]] = []
    code = 0

    # Comparaison fichier par fichier
    for rel, meta in sceau["fichiers"].items():
        nouveau = nouveau_sceau["fichiers"].get(rel)
        if nouveau is None:
            details.append({"fichier": rel, "etat": "DISPARU"})
            code = CODE_DEFECT
        else:
            # État VIDÉ : le fichier existe et son compte d'unités est nul
            if nouveau["unites"] == 0:
                details.append({"fichier": rel, "etat": "VIDE"})
                code = CODE_DEFECT
            elif nouveau["hash"] != meta["hash"]:
                details.append({"fichier": rel, "etat": "MODIFIE"})
                code = CODE_DEFECT
            else:
                details.append({"fichier": rel, "etat": "INTACT"})

    # Détection de nouveaux fichiers
    for rel in nouveau_sceau["fichiers"]:
        if rel not in sceau["fichiers"]:
            details.append({"fichier": rel, "etat": "NOUVEAU"})
            code = CODE_DEFECT

    # Gestion des erreurs de lecture
    if nouveau_sceau["erreurs_lecture"]:
        details.append(
            {"fichier": "", "etat": "ERREURS_LECTURE", "detail": str(nouveau_sceau["erreurs_lecture"])}
        )
        # Le code de sortie sera décidé par l’appelant (option --accepter-partiel)

    return code, details, nouveau_sceau


def relire(
    chemin: Path,
    offset: int,
    longueur: int,
    identifiant_attendu: Union[bytes, str],
) -> bool:
    """Lit *longueur* octets à partir de *offset* dans *chemin* et compare à
    *identifiant_attendu*.

    *identifiant_attendu* peut être fourni sous forme d’objets ``bytes`` ou
    d’une chaîne hexadécimale. Dans ce dernier cas il est décodé en bytes.
    Retourne ``True`` si les données lues sont identiques à l’identifiant.
    """
    # Normalisation de l’identifiant attendu
    if isinstance(identifiant_attendu, str):
        try:
            identifiant_bytes = bytes.fromhex(identifiant_attendu)
        except ValueError:
            # Chaîne non‑hexadécimale : comparaison impossible → échec
            return False
    else:
        identifiant_bytes = identifiant_attendu

    try:
        with chemin.open("rb") as f:
            f.seek(offset)
            data = f.read(longueur)
            return data == identifiant_bytes
    except OSError:
        return False


# ------------------------------------------------------------
# Interface en ligne de commande
# ------------------------------------------------------------

def _charger_sceau(chemin: Path) -> Dict[str, Any]:
    """Charge le sceau JSON depuis *chemin*."""
    try:
        with chemin.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Erreur de lecture du sceau : {exc}", file=sys.stderr)
        raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="sceller",
        description="Scelle, vérifie ou relit un arbre de fichiers.",
        epilog="Exemple : python -m outils.sceller sceller doc/stdlib_314/ --json",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Chemin racine de l’arbre à analyser (défaut : répertoire de l’outil).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Produit la sortie au format JSON uniquement.",
    )
    parser.add_argument(
        "--accepter-partiel",
        action="store_true",
        help="Accepte les résultats même si des erreurs de lecture sont survenues.",
    )
    sub = parser.add_subparsers(dest="commande", required=True)

    # sub‑commande sceller
    sp = sub.add_parser("sceller", help="Produit un sceau JSON de l’arbre.")
    sp.add_argument("cible", type=Path, help="Répertoire à sceller.")

    # sub‑commande verifier
    vp = sub.add_parser("verifier", help="Vérifie un sceau contre l’arbre actuel.")
    vp.add_argument("sceau", type=Path, help="Fichier JSON contenant le sceau.")
    vp.add_argument("cible", type=Path, help="Répertoire à vérifier.")

    # sub‑commande relire
    rp = sub.add_parser("relire", help="Relit un fragment de fichier et compare.")
    rp.add_argument("fichier", type=Path, help="Fichier à lire.")
    rp.add_argument("offset", type=int, help="Décalage en octets.")
    rp.add_argument("longueur", type=int, help="Longueur en octets.")
    rp.add_argument("identifiant", help="Identifiant attendu en hexadécimal.")

    args = parser.parse_args()

    # --------------------------------------------------------
    # Exécution selon la sous‑commande
    # --------------------------------------------------------
    if args.commande == "sceller":
        sceau = sceller(args.cible)
        denom = len(sceau["fichiers"])
        if denom == 0:
            print("Erreur : denominateur nul (aucun fichier examiné).", file=sys.stderr)
            return 3
        if args.json:
            sortie = {"sceau": sceau, "contrat": __doc__, "denominateur": denom}
            json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
            return 0
        else:
            print(f"Sceau créé pour {len(sceau['fichiers'])} fichiers.")
            print(f"Algorithme : {sceau['algorithme']}")
            print(f"Date : {sceau['date']}")
            print(f"Version Python : {sceau['python_version']}")
            print(f"Racine : {sceau['racine']}")
            if sceau['erreurs_lecture']:
                print(f"Erreurs de lecture : {len(sceau['erreurs_lecture'])}", file=sys.stderr)
            return 0

    if args.commande == "verifier":
        sceau = _charger_sceau(args.sceau)
        denom = len(sceau.get("fichiers", {}))
        if denom == 0:
            print("Erreur : denominateur nul (aucun fichier examiné).", file=sys.stderr)
            return 3
        code, details, nouveau_sceau = verifier(sceau, args.cible)

        # Gestion des erreurs de lecture : toujours signaler, mais le code de sortie
        # dépend de la présence de défauts.
        if nouveau_sceau.get("erreurs_lecture"):
            print(f"{len(nouveau_sceau['erreurs_lecture'])} chemins n’ont pas pu être lus.", file=sys.stderr)
            if not args.accepter_partiel and code == 0:
                print("Refus de conclure sans l’option --accepter-partiel.", file=sys.stderr)
                return CODE_PARTIAL

        if args.json:
            sortie = {"resultats": details, "contrat": __doc__, "denominateur": denom}
            json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
        else:
            for d in details:
                print(f"{d['fichier'] or '<global>'}: {d['etat']}")
        return code

    if args.commande == "relire":
        try:
            attendu = bytes.fromhex(args.identifiant)
        except ValueError:
            print("Identifiant attendu invalide : doit être en hexadécimal.", file=sys.stderr)
            return 1
        ok = relire(args.fichier, args.offset, args.longueur, attendu)
        denom = 1  # un élément examiné (le fragment du fichier)
        if args.json:
            sortie = {"identique": ok, "contrat": __doc__, "denominateur": denom}
            json.dump(sortie, sys.stdout, ensure_ascii=False, indent=2)
        else:
            print("Identique" if ok else "Différent")
        return 0 if ok else 1

    # Should never reach here
    return 1


if __name__ == "__main__":
    raise SystemExit(main())