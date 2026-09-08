"""Outil pour saisir un blocage de processus Python.

QUESTION       ce programme est‑il bloqué, et où exactement ?
MESURE         faulthandler au niveau descripteur, tous threads ; minuterie avec exit pour borner le temps
HYPOTHÈSES     la sortie est un vrai fichier ou stderr (fileno requis)
LIMITES        tue le PROCESSUS, pas un thread ni un sous‑interpréteur ; ne borne PAS la mémoire ; sur un blocage dans du code C sans retour au boucleur, la minuterie peut ne pas se déclencher
CONTRE-EXEMPLES StringIO comme sortie -> UnsupportedOperation ; et un `armer` sans cancel tue la tâche SUIVANTE ; un appel à faulthandler.dump_traceback_later portait un paramètre all_threads qui n'existe pas ; l'outil levait TypeError au moment précis où il devait sauver la session. Aucune porte de forme ne pouvait le voir : seule la confrontation à la signature réelle le montre.
INVOCATION
    {outil} borner --secondes 0.1 {fichier} --json
DOMAINE        un processus Python, sur cette machine
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import os
import subprocess
import sys
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator, TextIO

# Reconfiguration UTF‑8 si possible
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

CONTRAT = {
    "QUESTION": "ce programme est‑il bloqué, et où exactement ?",
    "MESURE": "faulthandler au niveau descripteur, tous threads ; minuterie avec exit pour borner le temps",
    "HYPOTHÈSES": "la sortie est un vrai fichier ou stderr (fileno requis)",
    "LIMITES": "tue le PROCESSUS, pas un thread ni un sous‑interpréteur ; ne borne PAS la mémoire ; sur un blocage dans du code C sans retour au boucleur, la minuterie peut ne pas se déclencher",
    "CONTRE-EXEMPLES": "StringIO comme sortie -> UnsupportedOperation ; et un `armer` sans cancel tue la tâche SUIVANTE ; un appel à faulthandler.dump_traceback_later portait un paramètre all_threads qui n'existe pas ; l'outil levait TypeError au moment précis où il devait sauver la session. Aucune porte de forme ne pouvait le voir : seule la confrontation à la signature réelle le montre.",
    "DOMAINE": "un processus Python, sur cette machine",
}


class ArgParseErreur(Exception):
    pass


class MonParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # pragma: no cover
        raise ArgParseErreur(message)


@contextmanager
def armer(secondes: float, sortie: TextIO = sys.stderr) -> Generator[None, None, None]:
    """Contexte qui arme une minuterie faulthandler et l’annule en sortie.

    Refuse les objets ne possédant pas de descripteur de fichier réel.
    """
    if not hasattr(sortie, "fileno"):
        raise ValueError(
            "La sortie doit être un vrai fichier (fileno requis), pas StringIO ou similaire."
        )
    try:
        _ = sortie.fileno()
    except Exception as exc:
        raise ValueError(
            f"La sortie ne fournit pas de fileno exploitable : {exc}"
        ) from exc

    faulthandler.dump_traceback_later(secondes, exit=True, file=sortie)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


def _run_monitored_process(commande: list[str], secondes: float) -> dict:
    """Lance une commande Python dans un sous‑processus surveillé.

    Retourne un dictionnaire décrivant le résultat.
    """
    script = commande[0]
    code_wrapper = f"""
import faulthandler, sys, traceback
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
faulthandler.dump_traceback_later({secondes}, exit=True, file=sys.stderr)
sys.argv = {commande!r}
try:
    with open({script!r}, 'r', encoding='utf-8') as f:
        source = f.read()
    code = compile(source, {script!r}, "exec")
    exec(code, {{"__name__": "__main__", "__file__": {script!r}}})
except SystemExit:
    pass
finally:
    faulthandler.cancel_dump_traceback_later()
"""
    try:
        proc = subprocess.Popen(
            [sys.executable, "-c", code_wrapper],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, stderr = proc.communicate()
    except Exception as e:
        return {"erreur": f"Échec du lancement: {e}", "code_sortie": 1}

    resultat: dict[str, Any] = {
        "sortie": stdout,
        "stderr": stderr,
        "code_sortie": proc.returncode,
    }

    if proc.returncode == 1:
        resultat["etat"] = f"INTERROMPU après {secondes}s"
        lignes = stderr.splitlines()
        for ligne in reversed(lignes):
            if ligne.lstrip().startswith("File"):
                resultat["bloque_a"] = ligne.strip()
                break
        for ligne in lignes:
            if ligne.startswith("Number of threads:"):
                try:
                    resultat["threads"] = int(ligne.split(":")[1].strip())
                except Exception:
                    pass
                break
    else:
        resultat["etat"] = "TERMINÉ"

    return resultat


def borner(commande: list[str], secondes: float) -> dict:
    """Lance une commande Python dans un sous‑processus borné en temps."""
    if secondes <= 0:
        return {
            "erreur": "Le dénominateur (secondes) est zéro ou négatif",
            "code_sortie": 2,
        }
    if not commande:
        return {"erreur": "Aucune commande fournie", "code_sortie": 1}
    script = commande[0]
    if not Path(script).is_file():
        return {"erreur": f"Script introuvable: {script}", "code_sortie": 1}
    return _run_monitored_process(commande, secondes)


def photographier(pid: int) -> dict:
    """Photographie tous les threads d’un processus (uniquement le processus courant)."""
    if pid != os.getpid():
        return {
            "erreur": "En stdlib pure, impossible de photographier un autre processus",
            "code_sortie": 1,
        }

    faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
    frames = sys._current_frames()
    threads = []
    for tid, frame in frames.items():
        stack = "".join(traceback.format_stack(frame))
        threads.append({"thread_id": tid, "stack": stack})

    return {
        "pid": pid,
        "threads": threads,
        "nombre_threads": len(threads),
        "code_sortie": 0,
    }


def _imprimer_humain(resultat: dict) -> None:
    if "erreur" in resultat:
        print(f"ERREUR: {resultat['erreur']}", file=sys.stderr)
        return
    if "etat" in resultat:
        print(f"état      {resultat['etat']}")
        if "bloque_a" in resultat:
            print(f"bloqué à  {resultat['bloque_a']}")
        if "threads" in resultat:
            print(f"threads   {resultat['threads']}")
        print(f"sortie    {resultat.get('sortie', '')}")
    elif "threads" in resultat:
        print(f"PID       {resultat['pid']}")
        print(f"threads   {resultat['nombre_threads']}")
        for t in resultat["threads"]:
            print(f"--- Thread {t['thread_id']} ---")
            print(t["stack"])


def main() -> int:
    json_demande = "--json" in sys.argv

    # Arguments communs transmis aux analyseurs parent et enfants
    parent_args = argparse.ArgumentParser(add_help=False)
    parent_args.add_argument(
        "--racine",
        type=Path,
        help="Racine du projet (surcharge la déduction automatique).",
        default=argparse.SUPPRESS,
    )
    parent_args.add_argument(
        "--json",
        action="store_true",
        help="Rend un objet JSON sur stdout.",
        default=argparse.SUPPRESS,
    )

    parser = MonParser(
        description="Saisir un blocage de processus Python via faulthandler.",
        epilog="Exemple: python saisir_blocage.py borner --secondes 1 script.py",
        parents=[parent_args],
    )
    subparsers = parser.add_subparsers(dest="sous_commande", required=True)

    borner_p = subparsers.add_parser(
        "borner",
        help="Lance une commande Python bornée en temps.",
        parents=[parent_args],
    )
    borner_p.add_argument("--secondes", type=float, required=True, help="Délai avant interruption.")
    borner_p.add_argument("cmd", nargs=argparse.REMAINDER, help="Commande Python à exécuter.")

    photo_p = subparsers.add_parser(
        "photographier",
        help="Photographie les threads d'un processus.",
        parents=[parent_args],
    )
    photo_p.add_argument("pid", type=int, help="Identifiant du processus (seul le courant est supporté).")

    armer_p = subparsers.add_parser(
        "armer",
        help="Exécute une commande dans un contexte armé et annulé.",
        parents=[parent_args],
    )
    armer_p.add_argument("--secondes", type=float, required=True, help="Délai avant interruption.")
    armer_p.add_argument("cmd", nargs=argparse.REMAINDER, help="Commande Python à exécuter.")

    try:
        args = parser.parse_args()
    except ArgParseErreur as e:
        if json_demande:
            print(json.dumps({"erreur": f"Erreur d'arguments: {e}", "denominateur": 0}, ensure_ascii=False))
        else:
            print(f"Erreur d'arguments: {e}", file=sys.stderr)
        return 2

    resultat: dict[str, Any] = {}
    exit_code = 0

    if args.sous_commande == "borner":
        if not args.cmd:
            resultat = {"erreur": "Commande vide", "code_sortie": 1}
            exit_code = 1
        else:
            resultat = borner(args.cmd, args.secondes)  # type: ignore[arg-type]
            exit_code = resultat.get("code_sortie", 0)
    elif args.sous_commande == "photographier":
        resultat = photographier(args.pid)  # type: ignore[arg-type]
        exit_code = resultat.get("code_sortie", 0)
    elif args.sous_commande == "armer":
        if not args.cmd:
            resultat = {"erreur": "Commande vide", "code_sortie": 1}
            exit_code = 1
        else:
            script = args.cmd[0]
            if not Path(script).is_file():
                resultat = {"erreur": f"Script introuvable: {script}", "code_sortie": 1}
                exit_code = 1
            else:
                try:
                    with armer(secondes=args.secondes):
                        with open(script, "r", encoding="utf-8") as f:
                            source = f.read()
                        code = compile(source, script, "exec")
                        exec(code, {"__name__": "__main__", "__file__": script})
                    resultat = {"etat": "TERMINÉ", "code_sortie": 0}
                    exit_code = 0
                except Exception as e:
                    resultat = {"erreur": f"Exception dans le contexte: {e}", "code_sortie": 1}
                    exit_code = 1
    else:
        resultat = {"erreur": "Sous-commande inconnue", "code_sortie": 1}
        exit_code = 1

    # Calcul du dénominateur
    denominateur = 0
    if args.sous_commande == "borner":
        denominateur = 1
    elif args.sous_commande == "armer":
        denominateur = 1
    elif args.sous_commande == "photographier":
        denominateur = resultat.get("nombre_threads", 0)

    if json_demande:
        sortie_json = {"denominateur": denominateur, "contrat": CONTRAT, "resultat": resultat}
        print(json.dumps(sortie_json, ensure_ascii=False, indent=2))
    else:
        _imprimer_humain(resultat)

    if denominateur == 0:
        # Refus légitime : dénominateur nul
        print("denominateur nul : aucun élément examiné", file=sys.stderr)
        return 3

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())