"""Éprouve les instruments avant qu'ils jugent les outils.

Pourquoi cet instrument ?  Le 1er octobre 2026, un outil qui lisait /etc/hosts
hors de son bac était bien classé FUITE par le juge d'isolation, mais le juge
rendait le code 0 : la CI et `docker build` l'auraient laissé passer. Le même
jour, la porte rendait 0 en `--json` sur un dossier vide, contre le socle §10.
Un verdict juste porté par un mauvais code de sortie est un instrument aveugle
pour tout ce qui l'enchaîne.

QUESTION      porte et juge rendent-ils le bon verdict ET le bon code de sortie
              sur des étalons dont on connaît la réponse ?
MESURE        verdict et code de sortie de chaque instrument sur quatre étalons
              engendrés à la volée, comparés à l'attendu écrit ici.
HYPOTHÈSES    `porte_qualite.py` et `test_isolation.py` sont à côté de ce fichier.
LIMITES       ne prouve que les cas étalonnés ; un défaut d'un autre contrôle
              (R1..R3, F3..F5) n'est pas couvert.
CONTRE-EXEMPLES  un juge qui rendrait 1 pour toute entrée passerait l'étalon
              fuyant ; c'est pourquoi l'étalon propre doit, lui, rendre 0.
DOMAINE       les deux instruments de ce dépôt, sur la plateforme qui l'exécute.

Codes de sortie : 0 tous les étalons conformes, 1 au moins un écart.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

ETALON_PROPRE = '''"""Étalon propre — doit être jugé LIVRABLE et CONFORME.

QUESTION      combien de lignes ce fichier texte compte-t-il ?
MESURE        nombre de lignes du fichier passé en argument
HYPOTHÈSES    le fichier est du texte UTF-8
LIMITES       aucune analyse du contenu
CONTRE-EXEMPLES  un fichier binaire est refusé, pas compté
DOMAINE       fichiers texte
INVOCATION
    {outil} {fichier} --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent


def compter(chemin: Path) -> int:
    """Rend le nombre de lignes du fichier."""
    return len(chemin.read_text(encoding="utf-8").splitlines())


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compte les lignes d'un fichier texte.",
        epilog="Exemple : python etalon.py notes.txt --json",
    )
    parser.add_argument("fichier", type=Path, help="fichier texte à compter")
    parser.add_argument("--racine", type=Path, default=RACINE, help="racine du projet")
    parser.add_argument("--json", action="store_true", help="sortie JSON")
    args = parser.parse_args()
#FUITE#
    if not args.fichier.is_file():
        print(f"refus : {args.fichier} n'est pas un fichier", file=sys.stderr)
        return 2
    try:
        lignes = compter(args.fichier)
    except UnicodeDecodeError:
        print(f"refus : {args.fichier} n'est pas du texte UTF-8", file=sys.stderr)
        return 2
    resultat = {"denominateur": 1, "examines": [args.fichier.name], "lignes": lignes}
    if args.json:
        print(json.dumps(resultat, ensure_ascii=False))
    else:
        print(f"{args.fichier.name} : {lignes} lignes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

# Lecture hors du bac : le chemin vient de l'environnement, jamais d'un littéral.
LIGNE_FUITE = '    Path(__import__("os").environ["ETALON_HORS_BAC"]).read_bytes()'
LITTERAL_EN_DUR = '    _ = "/home/etalon/chemin_en_dur"'


def _executer(cmd: List[str], cwd: Path, env: Dict[str, str]) -> tuple[int, str]:
    """Lance une commande et rend (code, stdout)."""
    cp = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                        encoding="utf-8", errors="replace", env=env, timeout=900)
    return cp.returncode, cp.stdout


def _verdict_juge(sortie: str) -> str:
    """Extrait le verdict du seul rapport rendu par le juge."""
    try:
        return json.loads(sortie)["rapports"][0]["verdict"]
    except (ValueError, KeyError, IndexError, TypeError):
        return "ILLISIBLE"


def _etat_porte(sortie: str) -> str:
    """Extrait l'état du seul jugement rendu par la porte, ou VIDE."""
    try:
        jugements = json.loads(sortie)["jugements"]
    except (ValueError, KeyError, TypeError):
        return "ILLISIBLE"
    return jugements[0]["etat"] if jugements else "VIDE"


def eprouver(racine: Path) -> Dict[str, Any]:
    """Rejoue les quatre étalons et compare à l'attendu."""
    bac = Path(tempfile.mkdtemp(prefix="etalons_"))
    hors_bac = Path(tempfile.mkdtemp(prefix="hors_bac_"))
    try:
        (bac / "vide").mkdir()
        cible = hors_bac / "secret.txt"
        cible.write_text("hors du bac\n", encoding="utf-8")
        env = dict(os.environ, ETALON_HORS_BAC=str(cible))

        sources = {
            "etalon_propre.py": ETALON_PROPRE.replace("#FUITE#\n", ""),
            "etalon_fuite.py": ETALON_PROPRE.replace("#FUITE#", LIGNE_FUITE),
            "etalon_en_dur.py": ETALON_PROPRE.replace("#FUITE#", LITTERAL_EN_DUR),
        }
        for nom, texte in sources.items():
            (bac / nom).write_text(texte, encoding="utf-8")

        juge = [sys.executable, str(racine / "test_isolation.py")]
        porte = [sys.executable, str(racine / "porte_qualite.py")]
        attendus = [
            ("juge", "etalon_propre.py", juge, "LIVRABLE", 0, _verdict_juge),
            ("juge", "etalon_fuite.py", juge, "FUITE", 1, _verdict_juge),
            ("porte", "etalon_propre.py", porte, "CONFORME", 0, _etat_porte),
            ("porte", "etalon_en_dur.py", porte, "MANQUEMENTS", 1, _etat_porte),
            ("porte", "vide", porte, "VIDE", 3, _etat_porte),
        ]
        cas = []
        for instrument, cible_nom, cmd, verdict_attendu, code_attendu, lire in attendus:
            code, sortie = _executer([*cmd, cible_nom, "--json"], bac, env)
            verdict = lire(sortie)
            cas.append({
                "instrument": instrument,
                "etalon": cible_nom,
                "verdict": verdict,
                "verdict_attendu": verdict_attendu,
                "code": code,
                "code_attendu": code_attendu,
                "conforme": verdict == verdict_attendu and code == code_attendu,
            })
    finally:
        shutil.rmtree(bac, ignore_errors=True)
        shutil.rmtree(hors_bac, ignore_errors=True)
    return {
        "denominateur": len(cas),
        "ecarts": sum(1 for c in cas if not c["conforme"]),
        "cas": cas,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Éprouve la porte et le juge sur des étalons à réponse connue.",
        epilog="Exemple : python mesures/eprouver_instruments.py --json",
    )
    parser.add_argument("--racine", type=Path, default=RACINE,
                        help="dossier contenant porte_qualite.py et test_isolation.py")
    parser.add_argument("--json", action="store_true", help="sortie JSON")
    args = parser.parse_args()

    resultat = eprouver(args.racine.resolve())
    if args.json:
        print(json.dumps(resultat, ensure_ascii=False, indent=2))
    else:
        for c in resultat["cas"]:
            marque = "OK " if c["conforme"] else "ÉCART"
            print(f"{marque} {c['instrument']:5} {c['etalon']:18} "
                  f"verdict {c['verdict']} (attendu {c['verdict_attendu']}), "
                  f"code {c['code']} (attendu {c['code_attendu']})")
        print(f"{resultat['denominateur'] - resultat['ecarts']}/{resultat['denominateur']} étalons conformes")
    return 1 if resultat["ecarts"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
