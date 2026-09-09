"""Partage d'état persistant entre processus/machines via Redis.

QUESTION      Peut-on partager un état ou exécuter des tâches sans bloquer le flux principal ?
MESURE        Temps de réponse et succès des opérations Redis (GET/SET/BLPOP) sous charge.
HYPOTHÈSES    Redis est accessible, les clés sont uniques, les valeurs sont sérialisables en JSON.
LIMITES       Ne gère pas les conflits de version (ex: deux processus écrivant la même clé).
CONTRE-EXEMPLES Un processus écrit une clé pendant qu'un autre la lit : la lecture peut retourner une valeur obsolète.
INVOCATION    {outil} client --cle test --valeur "{\"data\": 42}" --json
DOMAINE       Environnements multi-processus ou multi-machines avec Redis disponible.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Configuration initiale des flux
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

# Détection de Redis en mode dégradé
REDIS_DISPONIBLE = False
try:
    import redis
    from redis.exceptions import ConnectionError as RedisConnectionError
    REDIS_DISPONIBLE = True
except ImportError:
    REDIS_DISPONIBLE = False

# Analyseur parent pour --json et --racine
parent_parser = argparse.ArgumentParser(add_help=False)
parent_parser.add_argument(
    "--json", action="store_true", default=argparse.SUPPRESS,
    help="Sortie au format JSON (un seul objet)"
)
parent_parser.add_argument(
    "--racine", type=Path, default=argparse.SUPPRESS,
    help="Racine des chemins relatifs (défaut: répertoire du script)"
)

__all__ = [
    "stocker_etat",
    "verifier_cache",
    "executer_avec_backoff",
    "ajouter_tache_background",
    "main",
]

def _obtenir_racine(args: argparse.Namespace) -> Path:
    """Retourne la racine effective à partir des arguments."""
    racine = getattr(args, "racine", None)
    return racine if racine is not None else RACINE

def _sortie_json(resultat: Dict[str, Any], denominateur: int) -> None:
    """Affiche le résultat au format JSON avec le dénominateur."""
    resultat["denominateur"] = denominateur
    json.dump(resultat, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")

def _verifier_cle_valide(cle: str) -> None:
    """Vérifie que la clé est valide pour Redis."""
    if not cle or any(c in cle for c in " \t\n\r\0"):
        raise ValueError("Clé Redis invalide (espaces ou caractères interdits)")

def stocker_etat(
    cle: str,
    valeur: str,
    ttl: Optional[int] = None,
    racine: Optional[Path] = None,
) -> Dict[str, Any]:
    """Stocke un état dans Redis avec une clé et une durée de vie optionnelle.

    Args:
        cle: Clé Redis pour l'état partagé.
        valeur: Valeur à stocker (JSON sérialisé si complexe).
        ttl: Durée de vie en secondes (None = persistant).
        racine: Racine pour les chemins relatifs (non utilisé ici).

    Returns:
        Dictionnaire avec le statut et les détails.

    Raises:
        ValueError: Si la clé est invalide.
        RuntimeError: Si Redis n'est pas disponible.
    """
    _verifier_cle_valide(cle)

    if not REDIS_DISPONIBLE:
        raise RuntimeError("Redis non disponible, impossible de stocker l'état")

    try:
        r = redis.Redis()
        r.set(cle, valeur)
        if ttl is not None:
            r.expire(cle, ttl)
        return {"succes": True, "cle": cle, "valeur": valeur}
    except RedisConnectionError as e:
        raise RuntimeError(f"Échec de connexion à Redis: {e}") from e

def verifier_cache(
    cle: str,
    calculer: bool = False,
    racine: Optional[Path] = None,
) -> Dict[str, Any]:
    """Vérifie si un résultat est déjà en cache Redis.

    Args:
        cle: Clé du cache.
        calculer: Forcer le recalcul si absent.
        racine: Racine pour les chemins relatifs (non utilisé ici).

    Returns:
        Dictionnaire avec le statut, la valeur et si elle était présente.

    Raises:
        ValueError: Si la clé est invalide.
        RuntimeError: Si Redis n'est pas disponible.
    """
    _verifier_cle_valide(cle)

    if not REDIS_DISPONIBLE:
        raise RuntimeError("Redis non disponible, cache inaccessible")

    try:
        r = redis.Redis()
        valeur = r.get(cle)
        if valeur is not None:
            return {"succes": True, "valeur": valeur.decode("utf-8"), "present": True}
        if calculer:
            raise RuntimeError("Recalcul non implémenté (nécessite une fonction de calcul)")
        return {"succes": False, "valeur": None, "present": False}
    except RedisConnectionError as e:
        raise RuntimeError(f"Échec de connexion à Redis: {e}") from e

def executer_avec_backoff(
    commande: str,
    max_tentatives: int = 5,
    racine: Optional[Path] = None,
) -> Dict[str, Any]:
    """Exécute une commande Redis avec reconnexions exponentielles.

    Args:
        commande: Commande Redis à exécuter (ex: "GET ma_cle").
        max_tentatives: Nombre maximal de tentatives.
        racine: Racine pour les chemins relatifs (non utilisé ici).

    Returns:
        Dictionnaire avec le résultat ou l'erreur.

    Raises:
        ValueError: Si la commande est vide.
        RuntimeError: Si Redis n'est pas disponible.
    """
    if not commande.strip():
        raise ValueError("Commande Redis vide")

    if not REDIS_DISPONIBLE:
        raise RuntimeError("Redis non disponible, exécution impossible")

    try:
        r = redis.Redis()
        cmd_parts = commande.split()
        cmd = cmd_parts[0].upper()
        args = cmd_parts[1:]

        # Vérification basique des commandes supportées
        if cmd not in {"GET", "SET", "EXISTS", "EXPIRE", "RPUSH", "BLPOP", "LMOVE"}:
            raise ValueError(f"Commande Redis non supportée: {cmd}")

        for tentative in range(1, max_tentatives + 1):
            try:
                if cmd == "GET":
                    valeur = r.get(args[0])
                    return {"succes": True, "resultat": valeur.decode("utf-8") if valeur else None}
                elif cmd == "SET":
                    r.set(args[0], args[1])
                    return {"succes": True, "resultat": "OK"}
                elif cmd == "EXISTS":
                    existe = r.exists(args[0])
                    return {"succes": True, "resultat": bool(existe)}
                elif cmd == "EXPIRE":
                    r.expire(args[0], int(args[1]))
                    return {"succes": True, "resultat": "OK"}
                elif cmd == "RPUSH":
                    r.rpush(args[0], *args[1:])
                    return {"succes": True, "resultat": "OK"}
                elif cmd == "BLPOP":
                    valeur = r.blpop(args[0], timeout=int(args[1]))
                    return {"succes": True, "resultat": valeur[1].decode("utf-8") if valeur else None}
                elif cmd == "LMOVE":
                    r.lmove(args[0], args[1], args[2], args[3])
                    return {"succes": True, "resultat": "OK"}
            except RedisConnectionError:
                if tentative == max_tentatives:
                    raise
                attente = min(2 ** tentative, 10)  # Délai exponentiel plafonné à 10s
                time.sleep(attente)
        raise RuntimeError("Nombre maximal de tentatives atteint")
    except RedisConnectionError as e:
        raise RuntimeError(f"Échec après {max_tentatives} tentatives: {e}") from e

def ajouter_tache_background(
    liste: str,
    valeur: str,
    attendre: bool = False,
    racine: Optional[Path] = None,
) -> Dict[str, Any]:
    """Ajoute une tâche à une liste Redis pour traitement en arrière-plan.

    Args:
        liste: Nom de la liste Redis pour la tâche.
        valeur: Valeur à ajouter à la liste.
        attendre: Bloquer jusqu'à complétion.
        racine: Racine pour les chemins relatifs (non utilisé ici).

    Returns:
        Dictionnaire avec le statut et les détails.

    Raises:
        ValueError: Si la liste est vide.
        RuntimeError: Si Redis n'est pas disponible.
    """
    if not liste.strip():
        raise ValueError("Nom de liste Redis vide")

    if not REDIS_DISPONIBLE:
        raise RuntimeError("Redis non disponible, tâches en arrière-plan impossibles")

    try:
        r = redis.Redis()
        r.rpush(liste, valeur)
        if attendre:
            # Simulation d'attente (en réalité, il faudrait un worker séparé)
            raise RuntimeError("Attente de complétion non implémentée (nécessite un worker)")
        return {"succes": True, "liste": liste, "valeur": valeur}
    except RedisConnectionError as e:
        raise RuntimeError(f"Échec de connexion à Redis: {e}") from e

def _creer_analyseur() -> argparse.ArgumentParser:
    """Crée l'analyseur d'arguments principal."""
    analyseur = argparse.ArgumentParser(
        description="Partage d'état persistant entre processus via Redis.",
        parents=[parent_parser],
    )
    sous_analyseurs = analyseur.add_subparsers(dest="sous_commande", required=True)

    # Sous-commande client
    client_parser = sous_analyseurs.add_parser(
        "client", parents=[parent_parser],
        help="Stocker un état partagé dans Redis."
    )
    client_parser.add_argument(
        "--cle", type=str, required=True,
        help="Clé Redis pour l'état partagé (ex: 'config')"
    )
    client_parser.add_argument(
        "--valeur", type=str, required=True,
        help="Valeur à stocker (JSON sérialisé si complexe, ex: '{\"data\": 42}')"
    )
    client_parser.add_argument(
        "--ttl", type=int, default=None,
        help="Durée de vie en secondes (0 = persistant, défaut: persistant)"
    )

    # Sous-commande cache
    cache_parser = sous_analyseurs.add_parser(
        "cache", parents=[parent_parser],
        help="Vérifier ou forcer un cache Redis."
    )
    cache_parser.add_argument(
        "--cle", type=str, required=True,
        help="Clé du cache (ex: 'resultat_calcul')"
    )
    cache_parser.add_argument(
        "--calculer", action="store_true",
        help="Forcer le recalcul si absent (non implémenté)"
    )

    # Sous-commande backoff
    backoff_parser = sous_analyseurs.add_parser(
        "backoff", parents=[parent_parser],
        help="Exécuter une commande Redis avec reconnexions exponentielles."
    )
    backoff_parser.add_argument(
        "--commande", type=str, required=True,
        help="Commande Redis à exécuter (ex: 'GET ma_cle')"
    )
    backoff_parser.add_argument(
        "--max_tentatives", type=int, default=5,
        help="Nombre maximal de tentatives (défaut: 5)"
    )

    # Sous-commande background
    background_parser = sous_analyseurs.add_parser(
        "background", parents=[parent_parser],
        help="Ajouter une tâche à une liste Redis pour traitement en arrière-plan."
    )
    background_parser.add_argument(
        "--liste", type=str, required=True,
        help="Nom de la liste Redis (ex: 'taches')"
    )
    background_parser.add_argument(
        "--valeur", type=str, required=True,
        help="Valeur à ajouter à la liste (ex: 'traiter_fichier')"
    )
    background_parser.add_argument(
        "--attendre", action="store_true",
        help="Bloquer jusqu'à complétion (non implémenté)"
    )

    return analyseur

def main() -> int:
    """Point d'entrée principal."""
    analyseur = _creer_analyseur()

    # Gestion des erreurs d'analyse d'arguments sans quitter immédiatement
    try:
        args = analyseur.parse_args()
    except SystemExit as exc:
        # --help ou -h lève SystemExit(0), on laisse argparse gérer
        if exc.code == 0:
            raise
        # Refus : sous-commande invalide ou arguments manquants
        denominateur = 0
        json_flag = "--json" in sys.argv
        print("denominateur nul: sous_commande invalide ou arguments manquants", file=sys.stderr)
        if json_flag:
            _sortie_json({"erreur": "sous_commande invalide ou arguments manquants"}, denominateur)
        return 3

    racine = _obtenir_racine(args)
    resultat: Dict[str, Any] = {}
    denominateur = 0
    code_sortie = 0

    try:
        if args.sous_commande == "client":
            resultat = stocker_etat(
                cle=args.cle,
                valeur=args.valeur,
                ttl=args.ttl,
                racine=racine,
            )
            denominateur = 1
            if getattr(args, "json", False):
                _sortie_json(resultat, denominateur)
            else:
                print(f"État stocké avec la clé '{args.cle}'", file=sys.stderr)

        elif args.sous_commande == "cache":
            resultat = verifier_cache(
                cle=args.cle,
                calculer=args.calculer,
                racine=racine,
            )
            denominateur = 1
            if resultat["present"]:
                if getattr(args, "json", False):
                    _sortie_json({"valeur": resultat["valeur"]}, denominateur)
                else:
                    print(resultat["valeur"])
            else:
                print(f"Clé '{args.cle}' absente du cache", file=sys.stderr)
                code_sortie = 1

        elif args.sous_commande == "backoff":
            resultat = executer_avec_backoff(
                commande=args.commande,
                max_tentatives=args.max_tentatives,
                racine=racine,
            )
            denominateur = 1
            if getattr(args, "json", False):
                _sortie_json({"resultat": resultat["resultat"]}, denominateur)
            else:
                print(resultat["resultat"])

        elif args.sous_commande == "background":
            resultat = ajouter_tache_background(
                liste=args.liste,
                valeur=args.valeur,
                attendre=args.attendre,
                racine=racine,
            )
            denominateur = 1
            if getattr(args, "json", False):
                _sortie_json(resultat, denominateur)
            else:
                print(f"Tâche ajoutée à la liste '{args.liste}'", file=sys.stderr)

    except ValueError as e:
        print(f"Erreur: {e}", file=sys.stderr)
        code_sortie = 2
    except RuntimeError as e:
        print(f"Erreur: {e}", file=sys.stderr)
        if "Redis non disponible" in str(e):
            denominateur = 0
            print("denominateur nul: Redis requis mais inaccessible", file=sys.stderr)
            code_sortie = 3
        else:
            code_sortie = 2
    except Exception as e:
        print(f"Erreur inattendue: {e}", file=sys.stderr)
        code_sortie = 2

    # Gestion du refus (denominateur nul) lorsqu'aucune condition précédente n'a déjà fixé le code
    if denominateur == 0 and code_sortie != 3:
        print("denominateur nul: aucun élément examiné", file=sys.stderr)
        code_sortie = 3

    # Sortie JSON pour les erreurs si demandé
    if code_sortie != 0 and getattr(args, "json", False):
        # On récupère le dernier message d'erreur déjà imprimé sur stderr
        # (simplement le dernier texte après le dernier saut de ligne)
        dernier_err = sys.stderr.getvalue().splitlines()[-1] if hasattr(sys.stderr, "getvalue") else "Erreur"
        _sortie_json({"erreur": dernier_err}, denominateur)

    return code_sortie

if __name__ == "__main__":
    raise SystemExit(main())