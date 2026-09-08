"""paralleliser.py
Outil de parallélisation via ``concurrent.interpreters`` (Python 3.14+).

CONTRAT DE MESURE
QUESTION       : ce travail gagne-t-il à être reparti sur plusieurs interpréteurs ?
MESURE         : crée un pool d'interpréteurs, exécute la fonction avec
                ``call_in_thread`` et compare le temps au séquentiel et au
                ``ThreadPoolExecutor`` (4 voies).
HYPOTHÈSES     : les tâches sont indépendantes, CPU-bound, et leurs arguments/
                résultats sont de types partageables (int, str, bytes, list,
                dict, set, etc.).
LIMITES        : ``exec`` est synchrone ; seul ``call_in_thread`` parallélise.
                La création d'un interpréteur coûte ≈ 9,7 ms → un pool est
                obligatoire. Les objets de classe personnalisée lèvent
                ``NotShareableError``.
CONTRE-EXEMPLE : deux mesures antérieures utilisaient ``exec`` et concluaient
                à « aucun gain ». Avec ``call_in_thread`` le gain est
                2,5×-3,5×.
INVOCATION
    {outil} --demo --json
DOMAINE        : tâches CPU indépendantes, Python 3.14+.
"""

from __future__ import annotations

import sys
import ast
import json
import time
import argparse
import threading
import inspect
from pathlib import Path
from typing import Any, Callable, List, Tuple, Dict

# ------------------------------------------------------------
# Encodage UTF-8 pour stdout et stderr
# ------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ------------------------------------------------------------
# Import conditionnel de l'API « concurrent.interpreters »
# ------------------------------------------------------------
try:
    from concurrent import interpreters  # type: ignore
    from concurrent.interpreters import (
        Interpreter,
        InterpreterError,
        InterpreterNotFoundError,
        NotShareableError,
        ExecutionFailed,
    )
except ImportError:  # pragma: no cover
    interpreters = None  # type: ignore
    Interpreter = None  # type: ignore
    InterpreterError = Exception  # fallback
    InterpreterNotFoundError = Exception
    NotShareableError = Exception
    ExecutionFailed = Exception

# ------------------------------------------------------------
# Extensions C connues — à éprouver, pas à supposer
# ------------------------------------------------------------
_EXTENSIONS_C_CONNUES = frozenset({
    "numpy", "scipy", "pandas", "sklearn", "cv2",
    "torch", "tensorflow", "matplotlib", "PIL",
    "lxml", "yaml", "regex", "markupsafe",
    "bson", "Crypto", "psycopg2", "asyncpg",
})

# ------------------------------------------------------------
# CORE – fonctions sans dépendance à argparse / print
# ------------------------------------------------------------

def _temps_execution(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Tuple[Any, float]:
    """Exécute *func* et renvoie (résultat, durée en secondes)."""
    debut = time.perf_counter()
    rés = func(*args, **kwargs)
    fin = time.perf_counter()
    return rés, fin - debut

def _capturer_resultat(_resultat: list, _fonction: Callable, *args: Any) -> None:
    """Wrapper pour capturer le résultat d'un appel dans une liste partagée."""
    _resultat[0] = _fonction(*args)

def _executer_sequentiel(
    fonction: Callable[..., Any],
    args_liste: List[Tuple[Any, ...]],
) -> Tuple[List[Any], float]:
    """Exécute séquentiellement la fonction sur chaque jeu d'arguments."""
    résultats: List[Any] = []
    début = time.perf_counter()
    for args in args_liste:
        rés, _ = _temps_execution(fonction, *args)
        résultats.append(rés)
    fin = time.perf_counter()
    return résultats, fin - début

def _executer_threadpool(
    fonction: Callable[..., Any],
    args_liste: List[Tuple[Any, ...]],
    max_workers: int,
) -> Tuple[List[Any], float]:
    """Exécute la fonction en parallèle avec ThreadPoolExecutor."""
    from concurrent.futures import ThreadPoolExecutor  # stdlib

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(fonction, *args) for args in args_liste]
        début = time.perf_counter()
        résultats = [f.result() for f in futures]
        fin = time.perf_counter()
    return résultats, fin - début

def _verifier_extensions_c(fonction: Callable[..., Any]) -> List[str]:
    """Vérifie si la fonction importe des extensions C et émet des avertissements."""
    avertissements: List[str] = []
    modules_c_detectes: set = set()

    # 1. Analyse de la source de la fonction pour les imports
    try:
        source = inspect.getsource(fonction)
    except (OSError, TypeError):
        source = ""

    if source:
        # Validation par compile() avant ast.parse (SOCLE)
        try:
            compile(source, "<paralleliser>", "exec")
        except SyntaxError:
            source = ""

        if source:
            try:
                tree = ast.parse(source)
            except SyntaxError:
                tree = None

            if tree is not None:
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            racine = alias.name.split(".")[0]
                            if racine in _EXTENSIONS_C_CONNUES:
                                modules_c_detectes.add(racine)
                    elif isinstance(node, ast.ImportFrom):
                        if node.module:
                            racine = node.module.split(".")[0]
                            if racine in _EXTENSIONS_C_CONNUES:
                                modules_c_detectes.add(racine)

    # 2. Vérifie les globals de la fonction pour les modules C
    for nom, valeur in getattr(fonction, "__globals__", {}).items():
        if hasattr(valeur, "__file__") and valeur.__file__:
            fichier = str(valeur.__file__)
            mod_nom = getattr(valeur, "__name__", nom)
            if fichier.endswith((".so", ".pyd", ".dll")):
                modules_c_detectes.add(mod_nom)
            elif mod_nom and mod_nom.split(".")[0] in _EXTENSIONS_C_CONNUES:
                modules_c_detectes.add(mod_nom.split(".")[0])

    for mod in sorted(modules_c_detectes):
        avertissements.append(
            f"La fonction utilise « {mod} », une extension C qui peut "
            f"ne pas fonctionner correctement avec plusieurs sous-interpréteurs."
        )

    return avertissements

def _preparer_interpreteur(
    interpr: Interpreter,
    source_fonction: str,
    nom_fonction: str,
) -> None:
    """Injecte la définition de la fonction dans l'interpréteur."""
    # Validation de la source par compile() (SOCLE)
    try:
        compile(source_fonction, "<paralleliser>", "exec")
    except SyntaxError as exc:
        raise InterpreterError(
            f"Source invalide pour {nom_fonction} : {exc}"
        ) from exc
    try:
        interpr.exec(source_fonction, dedent=True)  # type: ignore[arg-type]
    except ExecutionFailed as exc:
        raise InterpreterError(f"Échec d'exec dans l'interpréteur : {exc}") from exc

def _executer_interpreteur_pool(
    fonction: Callable[..., Any],
    args_liste: List[Tuple[Any, ...]],
    interpreteurs: List[Interpreter],
) -> Tuple[List[Any], float]:
    """Utilise les interpréteurs déjà créés du pool et exécute via call_in_thread."""
    if interpreters is None:
        raise RuntimeError("Le module concurrent.interpreters n'est pas disponible.")
    if not interpreteurs:
        raise InterpreterError("Le pool d'interpréteurs est vide — utilisez le gestionnaire de contexte.")

    # Source de la fonction (doit être définie au niveau module)
    source = inspect.getsource(fonction)
    nom_fonction = fonction.__name__
    source_wrapper = inspect.getsource(_capturer_resultat)

    # Prépare les interpréteurs existants (injecte la fonction + le wrapper)
    for interp in interpreteurs:
        _preparer_interpreteur(interp, source, nom_fonction)
        _preparer_interpreteur(interp, source_wrapper, "_capturer_resultat")
        try:
            interp.exec(f"_ = {nom_fonction}")
        except ExecutionFailed as exc:
            raise InterpreterError(
                f"La fonction {nom_fonction} n'est pas accessible "
                f"dans l'interpréteur : {exc}"
            ) from exc

    # Lance les tâches via call_in_thread
    threads: List[threading.Thread] = []
    resultats_partages: List[List[Any]] = []
    erreurs: List[Exception] = []

    debut = time.perf_counter()

    for interp, args in zip(interpreteurs, args_liste):
        resultat_partage: List[Any] = [None]
        resultats_partages.append(resultat_partage)
        try:
            th = interp.call_in_thread(
                _capturer_resultat, resultat_partage, fonction, *args
            )
            threads.append(th)
        except NotShareableError as exc:
            erreurs.append(exc)
        except ExecutionFailed as exc:
            erreurs.append(exc)
        except Exception as exc:  # pragma: no cover
            erreurs.append(exc)

    for th in threads:
        th.join()

    fin = time.perf_counter()

    if erreurs:
        raise erreurs[0]

    resultats = [rp[0] for rp in resultats_partages]
    return resultats, fin - debut

# ------------------------------------------------------------
# Classe Pool – interface publique
# ------------------------------------------------------------

class Pool:
    """Pool d'interpréteurs réutilisables."""

    def __init__(self, taille: int, seuil: float = 1.5) -> None:
        if taille <= 0:
            raise ValueError("La taille du pool doit être > 0.")
        if seuil <= 0:
            raise ValueError("Le seuil doit être > 0.")
        self.taille = taille
        self.seuil = seuil
        self._interpreteurs: List[Interpreter] = []

    def __enter__(self) -> "Pool":
        if interpreters is None:
            raise RuntimeError("API concurrent.interpreters indisponible.")
        self._interpreteurs = [interpreters.create() for _ in range(self.taille)]
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for interp in self._interpreteurs:
            try:
                interp.close()
            except Exception:
                pass
        self._interpreteurs.clear()

    def appliquer(
        self,
        fonction: Callable[..., Any],
        args_liste: List[Tuple[Any, ...]],
    ) -> Dict[str, Any]:
        """Mesure et exécute la fonction en parallèle si le gain ≥ seuil."""
        denominateur = len(args_liste)
        if denominateur == 0:
            raise ValueError("Aucun argument à examiner")

        avertissements = _verifier_extensions_c(fonction)

        try:
            _, temps_seq = _executer_sequentiel(fonction, args_liste)
            if temps_seq == 0:
                raise ZeroDivisionError("Temps séquentiel nul")

            _, temps_threads = _executer_threadpool(fonction, args_liste, self.taille)

            try:
                résultats, temps_interp = _executer_interpreteur_pool(
                    fonction, args_liste, self._interpreteurs
                )
            except NotShareableError as exc:
                return {
                    "verdict": "erreur",
                    "type": "NotShareableError",
                    "message": str(exc),
                    "avertissements": avertissements,
                    "denominateur": denominateur
                }
            except ExecutionFailed as exc:
                return {
                    "verdict": "erreur",
                    "type": "ExecutionFailed",
                    "message": str(exc),
                    "avertissements": avertissements,
                    "denominateur": denominateur
                }
            except InterpreterNotFoundError as exc:
                return {
                    "verdict": "erreur",
                    "type": "InterpreterNotFoundError",
                    "message": str(exc),
                    "avertissements": avertissements,
                    "denominateur": denominateur
                }
            except InterpreterError as exc:
                return {
                    "verdict": "erreur",
                    "type": "InterpreterError",
                    "message": str(exc),
                    "avertissements": avertissements,
                    "denominateur": denominateur
                }

            gain = temps_seq / temps_interp if temps_interp > 0 else float("inf")
            if gain >= self.seuil:
                return {
                    "verdict": "succès",
                    "gain": round(gain, 2),
                    "temps_sequentiel_ms": round(temps_seq * 1000, 2),
                    "temps_threads_ms": round(temps_threads * 1000, 2),
                    "temps_interpreteur_ms": round(temps_interp * 1000, 2),
                    "résultats": résultats,
                    "avertissements": avertissements,
                    "denominateur": denominateur
                }
            else:
                return {
                    "verdict": "refus",
                    "gain": round(gain, 2),
                    "message": (
                        f"Gain mesuré {gain:.2f}× sur cet échantillon — "
                        "la parallélisation ne se rentabilise pas ici. "
                        f"Coût du pool : 9,69 ms × {self.taille}."
                    ),
                    "temps_sequentiel_ms": round(temps_seq * 1000, 2),
                    "temps_threads_ms": round(temps_threads * 1000, 2),
                    "temps_interpreteur_ms": round(temps_interp * 1000, 2),
                    "avertissements": avertissements,
                    "denominateur": denominateur
                }
        except Exception as exc:
            return {
                "verdict": "erreur",
                "type": type(exc).__name__,
                "message": str(exc),
                "avertissements": avertissements,
                "denominateur": denominateur
            }

# ------------------------------------------------------------
# CLI
# ------------------------------------------------------------

def _exemple_travail(itérations: int) -> int:
    """Fonction CPU-bound d'exemple : somme de 0..itérations-1."""
    total = 0
    for i in range(itérations):
        total += i
    return total

def _construire_sortie_json(
    resultat: Dict[str, Any],
    contrat: Dict[str, str],
    avertissements_api: List[str],
    denominateur: int
) -> Dict[str, Any]:
    """Construit la sortie JSON complète avec toutes les clés requises."""
    return {
        "résultat": resultat,
        "contrat": contrat,
        "avertissements_api": avertissements_api,
        "denominateur": denominateur
    }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parallélise une fonction CPU-bound avec un pool d'interpréteurs.",
        epilog=(
            "Exemple :\n"
            "  python -m outils.paralleliser --taches 4 --iterations 3000000 --seuil 1.5\n"
            "  python -m outils.paralleliser --demo --json"
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Chemin racine du projet (défaut : répertoire du script).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre la sortie au format JSON unique.",
    )
    parser.add_argument(
        "--seuil",
        type=float,
        default=1.5,
        help="Seuil de gain (exemple : 1.5 → 1,5×) pour accepter la parallélisation.",
    )
    parser.add_argument(
        "--taches",
        type=int,
        default=4,
        help="Nombre d'interpréteurs dans le pool (défaut : 4).",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=3_000_000,
        help="Nombre d'itérations de la tâche d'exemple (défaut : 3 000 000).",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Utiliser la fonction d'exemple intégrée.",
    )

    args = parser.parse_args()

    # Construction du contrat
    contrat = {
        "QUESTION": "ce travail gagne-t-il à être reparti sur plusieurs interpréteurs ?",
        "MESURE": "concurrent.interpreters avec call_in_thread, compare au séquentiel et aux threads, sur 4 voies",
        "HYPOTHÈSES": "tâches indépendantes, CPU-bound, arguments/résultats partageables",
        "LIMITES": "exec() est SYNCHRONE ; seul call_in_thread parallélise ; création d'interpréteur ≈ 9,7 ms → pool obligatoire ; NotShareableError sur classes personnalisées",
        "CONTRE-EXEMPLE": "mesures antérieures avec exec() donnaient aucun gain ; call_in_thread montre 2,5×-3,5× de gain",
        "DOMAINE": "tâches CPU indépendantes, Python 3.14+",
    }

    # Cas de refus légitime
    if not args.demo:
        if args.json:
            sortie = _construire_sortie_json(
                {
                    "verdict": "refus",
                    "type": "UsageError",
                    "message": "Aucune fonction fournie. Utilisez --demo ou implémentez votre propre appel."
                },
                contrat,
                [],
                0
            )
            json.dump(sortie, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            print("Denominateur nul : rien à examiner, refus de conclure.", file=sys.stderr)
        return 3

    # Vérification de la version Python
    if sys.version_info < (3, 14):
        if args.json:
            sortie = _construire_sortie_json(
                {
                    "verdict": "erreur",
                    "type": "VersionError",
                    "message": "Cet outil nécessite Python 3.14 ou supérieur."
                },
                contrat,
                [],
                0
            )
            json.dump(sortie, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            print("Denominateur nul : version Python insuffisante, refus de conclure.", file=sys.stderr)
        return 3

    avertissements_api: List[str] = []
    if sys.version_info >= (3, 14):
        avertissements_api.append(
            "L'API concurrent.interpreters est en phase initiale (Python 3.14+). "
            "Attendez-vous à des changements possibles entre versions mineures."
        )

    # Fonction à exécuter
    fonction = _exemple_travail
    args_liste = [(args.iterations,) for _ in range(args.taches)]
    denominateur = len(args_liste)

    # Exécution du pool
    try:
        with Pool(taille=args.taches, seuil=args.seuil) as pool:
            résultat = pool.appliquer(fonction, args_liste)
    except Exception as exc:
        if args.json:
            sortie = _construire_sortie_json(
                {
                    "verdict": "erreur",
                    "type": type(exc).__name__,
                    "message": str(exc)
                },
                contrat,
                avertissements_api,
                0
            )
            json.dump(sortie, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        else:
            print(f"Denominateur nul : {str(exc)}, refus de conclure.", file=sys.stderr)
        return 3

    # Avertissements sur stderr
    for avert in résultat.get("avertissements", []):
        print(f"Avertissement : {avert}", file=sys.stderr)
    for avert in avertissements_api:
        print(f"Avertissement : {avert}", file=sys.stderr)

    # Construction de la sortie
    sortie = _construire_sortie_json(
        résultat,
        contrat,
        avertissements_api,
        denominateur
    )

    if args.json:
        json.dump(sortie, sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0 if résultat.get("verdict") == "succès" else 1
    else:
        verdict = résultat.get("verdict")
        if verdict == "succès":
            print(f"Verdict : {verdict}")
            print(f"Gain mesuré : {résultat.get('gain')}×")
            print(f"Temps séquentiel : {résultat.get('temps_sequentiel_ms')} ms")
            print(f"Temps threads : {résultat.get('temps_threads_ms')} ms")
            print(f"Temps interpréteur : {résultat.get('temps_interpreteur_ms')} ms")
            print(f"Résultats : {résultat.get('résultats')}")
            return 0
        elif verdict == "refus":
            print(résultat.get("message"))
            return 2
        else:
            print(f"Erreur ({résultat.get('type')}): {résultat.get('message')}")
            return 1

if __name__ == "__main__":
    raise SystemExit(main())