"""suivre_tache – suivi d’identités de tâches concurrentes

QUESTION
    laquelle de mes N tâches parallèles a produit cette ligne ?
MESURE
    contextvars pour porter l'identité, logging.Filter pour l'injecter
HYPOTHÈSES
    les tâches sont lancées dans ce processus (threads ou asyncio)
LIMITES
    ne traverse PAS un sous‑processus ; un %(tache)s sans filtre lève KeyError
CONTRE-EXEMPLES
    threading.local() rend la même valeur aux 4 tâches asyncio
INVOCATION
    {outil} relire {dossier}/journal.json --tache agent-42 --json
DOMAINE
    un processus Python, threads et tâches asyncio
"""

from __future__ import annotations

import argparse
import contextvars
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Tuple

# ------------------------------------------------------------
# Encodage UTF‑8 pour stdout et stderr (règle 2 du socle)
# ------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

# ------------------------------------------------------------
# Gestion du contexte (core, sans argparse, sans print)
# ------------------------------------------------------------
_ContextVarMap: Dict[str, contextvars.ContextVar[Any]] = {}

def _obtenir_var(nom: str, default: Any) -> contextvars.ContextVar[Any]:
    """Retourne (ou crée) la ContextVar nommée *nom* avec *default*."""
    if nom not in _ContextVarMap:
        if default is None:
            raise ValueError("default obligatoire")
        _ContextVarMap[nom] = contextvars.ContextVar(nom, default=default)
    return _ContextVarMap[nom]

class _FiltreContexte(logging.Filter):
    """Injecte les valeurs du contexte dans chaque LogRecord."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        """Modifie *record* en ajoutant les variables de contexte demandées."""
        for cle, var in _ContextVarMap.items():
            try:
                valeur = var.get()
            except LookupError:
                valeur = ""
            setattr(record, cle, valeur)
        # garantir la présence de « tache » pour le formatteur JSON
        if not hasattr(record, "tache"):
            setattr(record, "tache", "")
        return True

class _JsonFormatter(logging.Formatter):
    """Formateur qui sérialise chaque enregistrement en JSON valide.

    Utilise json.dumps pour garantir l'échappement correct des
    caractères spéciaux dans tous les champs, notamment le message.
    """

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        data = {
            "tache": getattr(record, "tache", ""),
            "niveau": record.levelname,
            "message": message,
            "fonction": record.funcName,
            "ligne": record.lineno,
        }
        return json.dumps(data, ensure_ascii=False)

class contexte:
    """Gestionnaire de contexte qui fixe des variables *contextvars*.

    Exemple d'usage :
    ```python
    from suivre_tache import contexte, journal

    logger = journal(json_mode=False)
    with contexte(tache="agent-42", lot=3):
        logger.info("début du travail")
        # …
    ```
    Tous les enregistrements émis dans le bloc portent les valeurs indiquées.
    Les valeurs sont restaurées même en cas d'exception.
    """

    def __init__(self, **valeurs: Any) -> None:
        self._valeurs = valeurs
        self._tokens: List[Tuple[contextvars.ContextVar[Any], contextvars.Token]] = []

    def __enter__(self) -> "contexte":
        for cle, val in self._valeurs.items():
            var = _obtenir_var(cle, val)
            token = var.set(val)
            self._tokens.append((var, token))
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: D401
        """Restaure les valeurs précédentes, même si une exception a eu lieu."""
        for var, token in reversed(self._tokens):
            var.reset(token)

def _transmettre_identite() -> None:
    """Transmet l'identité du contexte courant aux sous‑processus via env."""
    identite: Dict[str, str] = {}
    for cle, var in _ContextVarMap.items():
        try:
            identite[cle] = str(var.get())
        except LookupError:
            continue

    if not identite:
        sys.stderr.write(
            "Avertissement: aucune identité de tâche définie dans le contexte pour transmission à un sous‑processus.\n"
        )
        return

    for cle, val in identite.items():
        os.environ[f"SUIVRE_TACHE_{cle.upper()}"] = val

# ------------------------------------------------------------
# Configuration du logger (core) – utilisé par la CLI
# ------------------------------------------------------------
def journal(
    nom: str = "suivre_tache",
    niveau: int = logging.INFO,
    json_mode: bool = False,
) -> logging.Logger:
    """Retourne un logger configuré.

    - *json_mode* : si vrai, chaque enregistrement est émis sous forme d'un
      objet JSON sur une ligne.
    - Le logger écrit sur *sys.stderr* (règle 3 du socle) et ne propage pas
      vers la racine.
    - Un filtre injecte toutes les variables de contexte.
    """
    logger = logging.getLogger(nom)
    logger.setLevel(niveau)
    logger.propagate = False

    # Gestion du handler unique (évite les doubles émissions)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        if json_mode:
            formatter = _JsonFormatter()
        else:
            formatter = logging.Formatter(
                fmt="%(asctime)s | %(tache)s | %(levelname)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    # Ajouter le filtre une seule fois
    if not any(isinstance(f, _FiltreContexte) for f in logger.filters):
        logger.addFilter(_FiltreContexte())
    return logger

# ------------------------------------------------------------
# Lecture d'un journal (core)
# ------------------------------------------------------------
def relire(
    fichier: Path,
    tache: str,
) -> Tuple[int, List[Mapping[str, Any]]]:
    """Lit *fichier* (JSON‑lines) et renvoie le nombre total de lignes lues
    et les enregistrements dont tache==*tache*.

    Retourne (total, resultats) où total est le nombre de lignes non vides.
    Retourne (0, []) si le fichier est vide.
    Lève *FileNotFoundError* si le fichier n'existe pas.
    """
    resultats: List[Mapping[str, Any]] = []
    total = 0
    with fichier.open("r", encoding="utf-8") as f:
        for ligne in f:
            ligne = ligne.strip()
            if not ligne:
                continue
            total += 1
            try:
                data = json.loads(ligne)
            except json.JSONDecodeError:
                continue
            if data.get("tache") == tache:
                resultats.append(data)
    return total, resultats

# ------------------------------------------------------------
# Interface en ligne de commande (CLI)
# ------------------------------------------------------------
def _chemin_racine(arg: str) -> Path:
    """Convertit l'argument *--racine* en Path absolu."""
    return Path(arg).expanduser().resolve()

class _Analyseur(argparse.ArgumentParser):
    """Analyseur qui renvoie un JSON d'erreur exploitable si --json est présent."""
    def error(self, message: str) -> None:
        if '--json' in sys.argv:
            json.dump({"erreur": message, "denominateur": 0}, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        sys.stderr.write(f"erreur: {message}\n")
        raise SystemExit(2)

def _executer_commande(
    args: argparse.Namespace,
    logger: logging.Logger,
) -> int:
    """Exécute la commande demandée dans un contexte figé.

    Codes de retour :
    - 0 : succès, rien à signaler.
    - 1 : tâche non trouvée (lignes présentes mais aucune ne correspond).
    - 2 : fichier manquant ou erreur d'arguments.
    - 3 : dénominateur nul ou erreur interne.
    """
    if args.commande == "relire":
        try:
            total, lignes = relire(args.fichier, args.tache)
        except FileNotFoundError:
            msg = f"Fichier introuvable : {args.fichier}"
            logger.error(msg)
            if args.json:
                json.dump({"erreur": msg, "denominateur": 0}, sys.stdout, ensure_ascii=False)
                sys.stdout.write("\n")
            return 2

        if total == 0:
            msg = f"Dénominateur nul : {args.fichier} est vide ou ne contient aucune ligne valide. Refus de traitement."
            logger.error(msg)
            if args.json:
                json.dump({"erreur": msg, "denominateur": 0}, sys.stdout, ensure_ascii=False)
                sys.stdout.write("\n")
            return 3

        sortie = {
            "denominateur": total,
            "resultat": lignes,
            "contrat": {
                "QUESTION": "laquelle de mes N tâches parallèles a produit cette ligne ?",
                "MESURE": "contextvars pour porter l'identité, logging.Filter pour l'injecter",
                "HYPOTHÈSES": "les tâches sont lancées dans ce processus (threads ou asyncio)",
                "LIMITES": "ne traverse PAS un sous‑processus ; un %(tache)s sans filtre lève KeyError",
                "CONTRE-EXEMPLES": "threading.local() rend la même valeur aux 4 tâches asyncio",
                "DOMAINE": "un processus Python, threads et tâches asyncio",
            },
        }

        if args.json:
            try:
                json.dump(sortie, sys.stdout, ensure_ascii=False)
                sys.stdout.write("\n")
            except Exception as e:  # pragma: no cover
                logger.error(f"Erreur d'encodage JSON : {e}")
                return 3
        else:
            for rec in lignes:
                sys.stderr.write(
                    f"{rec.get('fonction', '<unknown>')}:{rec.get('ligne', '?')} – "
                    f"{rec.get('niveau', rec.get('levelname', 'INFO'))} – "
                    f"{rec.get('message')}\n"
                )
        return 0 if lignes else 1

    # commande non reconnue (impossible grâce à required=True)
    return 4

def main(args: List[str] | None = None) -> int:
    """Point d'entrée de l'outil.

    Codes de retour :
    - 0 : succès, rien à signaler.
    - 1 : tâche non trouvée (lignes présentes mais aucune ne correspond).
    - 2 : fichier manquant ou erreur d'arguments.
    - 3 : dénominateur nul ou erreur interne.
    - 4 : commande non reconnue.
    """
    if args is None:
        args = sys.argv[1:]

    parser = _Analyseur(
        description="Outil de suivi d'identités de tâches concurrentes.",
        add_help=True,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--racine",
        type=_chemin_racine,
        default=RACINE,
        help="Chemin racine du projet (défaut : répertoire du script).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Émettre le résultat au format JSON unique.",
    )
    subparsers = parser.add_subparsers(dest="commande", required=True, parser_class=_Analyseur)

    # sous‑commande relire
    sp_relire = subparsers.add_parser(
        "relire",
        help="Reconstitue le fil d'une tâche depuis un journal JSON‑lines.",
    )
    sp_relire.add_argument(
        "fichier",
        type=Path,
        help="Fichier de journal à analyser.",
    )
    sp_relire.add_argument(
        "--tache",
        required=True,
        help="Identifiant de la tâche recherchée.",
    )
    sp_relire.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Émettre le résultat au format JSON unique.",
    )

    try:
        args = parser.parse_args(args)
    except SystemExit as e:
        if e.code != 0:
            sys.stderr.write("Dénominateur nul : arguments invalides. Refus de traitement.\n")
            if '--json' in sys.argv:
                json.dump({"erreur": "Arguments invalides", "denominateur": 0}, sys.stdout, ensure_ascii=False)
                sys.stdout.write("\n")
            return 3
        raise

    # transmission éventuelle d'identité avant tout sous‑processus éventuel
    _transmettre_identite()

    # logger utilisé pour les diagnostics (stderr)
    logger = journal(json_mode=getattr(args, "json", False))

    # Exécuter la commande dans un contexte figé (copy_context)
    return contextvars.copy_context().run(_executer_commande, args, logger)

if __name__ == "__main__":
    raise SystemExit(main())