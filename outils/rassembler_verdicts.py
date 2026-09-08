r"""QUESTION
    Que disent, ensemble, tous les outils passés sur ce dépôt ?

MESURE
    SARIF 2.1.0 émis par chaque outil, fusionné en un document unique ;
    chaque « run » porte le sceau SHA‑256 des sources analysées.

HYPOTHÈSES
    Chaque outil sait localiser son constat (fichier, ligne) et produit un
    SARIF conforme aux spécifications du standard.

LIMITES
    SARIF décrit des constats localisables : un verdict global sans fichier
    (ex. « maintenabilité 13,7 ») doit être attaché à la ligne 1, ce qui est
    une convention, pas une vérité. Le format ne dit rien de la gravité
    réelle au‑delà des niveaux « none », « note », « warning », « error ».

CONTRE-EXEMPLES
    Un `uri` contenant un chemin Windows (`outils\x.py`) est refusé par les
    consommateurs stricts ; il doit être converti en chemin POSIX.

INVOCATION
    {outil} emettre dummy 0 --verdicts R note "msg" {fichier} --json

DOMAINE
    Constats localisables dans des fichiers texte, agrégés pour les
    plateformes d’intégration continue (GitHub, Sonar, etc.).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Mapping

try:
    import xml.parsers.expat
except ImportError:
    xml = None

# ------------------------------------------------------------
# Encodage de la console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ------------------------------------------------------------
# Vérification de la version d'Expat
if xml is not None:
    if xml.parsers.expat.version_info < (2, 7, 2):
        sys.stderr.write(
            "Version d'Expat trop ancienne (< 2.7.2) ; lecture de XML non fiable.\n"
        )
        if "--json" in sys.argv:
            json.dump(
                {"erreur": "Version d'Expat trop ancienne", "denominateur": 0},
                sys.stdout,
                ensure_ascii=False,
            )
            sys.stdout.write("\n")
        raise SystemExit(1)
else:
    sys.stderr.write(
        "Module xml.parsers.expat non disponible ; lecture de XML désactivé.\n"
    )
    if "--json" in sys.argv:
        json.dump(
            {"erreur": "Module xml.parsers.expat non disponible", "denominateur": 0},
            sys.stdout,
            ensure_ascii=False,
        )
        sys.stdout.write("\n")
    raise SystemExit(1)

# ------------------------------------------------------------
# Constantes
SCHEMA_URL = "https://json.schemastore.org/sarif-2.1.0.json"
SARIF_VERSION = "2.1.0"
NIVEAUX_ADMIS = {"none", "note", "warning", "error"}
TAILLE_MAX = 5_000_000  # 5 Mo
PROFONDEUR_MAX = 1000   # estimation très grossière

# ------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Verdict:
    regle: str
    niveau: str
    message: str
    fichier: str | None = None
    ligne: int = 1

def _normaliser_uri(chemin: str) -> str:
    """Convertit un chemin Windows éventuel en URI POSIX."""
    return Path(chemin).as_posix()

def _hash_sources(racine: Path) -> str:
    """Calcule le SHA‑256 de l’arborescence de fichiers texte sous *racine*."""
    h = hashlib.sha256()
    for p in sorted(racine.rglob("*")):
        if p.is_file() and p.suffix.lower() in {".py", ".txt", ".md", ".json"}:
            try:
                with p.open("rb") as f:
                    while chunk := f.read(8192):
                        h.update(chunk)
            except OSError:
                continue
    return h.hexdigest()

def emettre(
    nom_outil: str,
    version_outil: str,
    verdicts: Iterable[Verdict],
    racine: Path,
) -> Mapping[str, object]:
    """Produit un document SARIF contenant un seul *run*."""
    verdicts = list(verdicts)
    rules: dict[str, dict] = {}
    results: List[dict] = []

    for v in verdicts:
        if v.niveau not in NIVEAUX_ADMIS:
            raise ValueError(f"Niveau non admis : {v.niveau!r}")

        if v.regle not in rules:
            rules[v.regle] = {
                "id": v.regle,
                "name": v.regle,
                "shortDescription": {"text": v.regle},
            }

        uri = _normaliser_uri(v.fichier) if v.fichier else "unknown"
        result = {
            "ruleId": v.regle,
            "level": v.niveau,
            "message": {"text": v.message},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": uri},
                        "region": {"startLine": v.ligne or 1},
                    }
                }
            ],
        }
        results.append(result)

    run: dict = {
        "tool": {
            "driver": {
                "name": nom_outil,
                "version": version_outil,
                "informationUri": "",
                "rules": list(rules.values()),
            }
        },
        "results": results,
        "automationDetails": {"id": _hash_sources(racine)},
    }

    return {"$schema": SCHEMA_URL, "version": SARIF_VERSION, "runs": [run]}

def _estimation_profondeur(contenu: str) -> int:
    """Compte grossièrement le nombre d’accolades ouvrantes consécutives."""
    max_depth = cur = 0
    for ch in contenu:
        if ch == "{":
            cur += 1
            if cur > max_depth:
                max_depth = cur
        elif ch == "}":
            cur = max(0, cur - 1)
    return max_depth

def charger_sarif(chemin: Path) -> Mapping[str, object]:
    """Lit un fichier SARIF en appliquant les garde‑fous de taille et de profondeur."""
    if not chemin.is_file():
        raise FileNotFoundError(str(chemin))
    if chemin.stat().st_size > TAILLE_MAX:
        raise ValueError(
            f"Taille du fichier {chemin} excède la limite de {TAILLE_MAX} octets"
        )
    texte = chemin.read_text(encoding="utf-8")
    if _estimation_profondeur(texte) > PROFONDEUR_MAX:
        raise ValueError(
            f"Profondeur estimée du JSON de {chemin} trop élevée"
        )
    return json.loads(texte)

def rassembler(chemins: Iterable[Path], racine: Path) -> Mapping[str, object]:
    """Fusionne plusieurs documents SARIF en un seul, un *run* par outil."""
    runs: List[dict] = []
    for p in chemins:
        sarif = charger_sarif(p)
        if not isinstance(sarif, dict) or "runs" not in sarif:
            raise ValueError(f"Document SARIF invalide : {p}")
        for run in sarif["runs"]:
            if "automationDetails" not in run:
                run["automationDetails"] = {"id": _hash_sources(racine)}
            runs.append(run)
    return {"$schema": SCHEMA_URL, "version": SARIF_VERSION, "runs": runs}

def trier(sarif: Mapping[str, object], niveau: str) -> Mapping[str, object]:
    """Retourne un SARIF ne contenant que les résultats du *niveau* indiqué."""
    if niveau not in NIVEAUX_ADMIS:
        raise ValueError(f"Niveau non admis : {niveau!r}")
    nouveau = {"$schema": SCHEMA_URL, "version": SARIF_VERSION, "runs": []}
    for run in sarif.get("runs", []):
        filtres = [r for r in run.get("results", []) if r.get("level") == niveau]
        if filtres:
            nouveau_run = dict(run)
            nouveau_run["results"] = filtres
            nouveau["runs"].append(nouveau_run)
    return nouveau

def _compter_niveaux(sarif: Mapping[str, object]) -> dict[str, int]:
    """Renvoie le décompte des niveaux présents dans le SARIF."""
    compte = {lvl: 0 for lvl in NIVEAUX_ADMIS}
    for run in sarif.get("runs", []):
        for r in run.get("results", []):
            lvl = r.get("level")
            if lvl in compte:
                compte[lvl] += 1
    return compte

def _affichage_humain(sarif: Mapping[str, object]) -> str:
    """Produit un résumé lisible par un humain."""
    nb_runs = len(sarif.get("runs", []))
    total = sum(len(run.get("results", [])) for run in sarif.get("runs", []))
    compte = _compter_niveaux(sarif)
    lignes = [
        f"{nb_runs} outil(s) · {total} constat(s)",
        "    "
        + "    ".join(f"{lvl:<7}{compte[lvl]:>4}" for lvl in ("error", "warning", "note", "none")),
    ]
    fichiers: dict[str, int] = {}
    for run in sarif.get("runs", []):
        for r in run.get("results", []):
            loc = r.get("locations", [{}])[0].get("physicalLocation", {})
            uri = loc.get("artifactLocation", {}).get("uri", "unknown")
            fichiers[uri] = fichiers.get(uri, 0) + 1
    for uri, nb in sorted(fichiers.items(), key=lambda it: -it[1])[:10]:
        lignes.append(f"{uri:<30} {nb:>3} constat(s)")
    return "\n".join(lignes)

class ArgumentParserJson(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        sys.stderr.write(f"erreur: {message}\n")
        if "--json" in sys.argv:
            json.dump({"erreur": message, "denominateur": 0}, sys.stdout, ensure_ascii=False)
            sys.stdout.write("\n")
        raise SystemExit(2)

def main() -> int:
    # parent parser contenant les options communes aux sous‑commandes
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument(
        "--json",
        action="store_true",
        help="Écrire le résultat en JSON sur stdout.",
        default=argparse.SUPPRESS,
    )
    parent_parser.add_argument(
        "--racine",
        type=Path,
        help="Répertoire racine du projet (défaut : répertoire du script).",
        default=argparse.SUPPRESS,
    )

    parser = ArgumentParserJson(
        description="Agrège les verdicts SARIF de plusieurs outils.",
        epilog="Exemple : python -m rassembler_verdicts rassembler *.sarif --json",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Répertoire racine du projet (défaut : répertoire du script).",
    )
    sub = parser.add_subparsers(dest="commande", required=True)

    # sous‑commande emettre
    em = sub.add_parser(
        "emettre",
        help="Émettre un SARIF à partir de Verdicts.",
        parents=[parent_parser],
    )
    em.add_argument("outil", help="Nom de l’outil.")
    em.add_argument("version", help="Version de l’outil.")
    em.add_argument(
        "--verdicts",
        nargs=4,
        action="append",
        metavar=("REGLE", "NIVEAU", "MESSAGE", "FICHIER"),
        help="Un verdict : règle, niveau, message, fichier (ligne=1). Répéter.",
    )

    # sous‑commande rassembler
    rs = sub.add_parser(
        "rassembler",
        help="Fusionner plusieurs fichiers SARIF.",
        parents=[parent_parser],
    )
    rs.add_argument("sarifs", nargs="+", type=Path, help="Chemins vers les fichiers SARIF.")
    rs.add_argument("--trier", choices=sorted(NIVEAUX_ADMIS), help="Filtrer les résultats par niveau après fusion.")

    # sous‑commande trier
    tr = sub.add_parser(
        "trier",
        help="Filtrer un SARIF existant.",
        parents=[parent_parser],
    )
    tr.add_argument("sarif", type=Path, help="Fichier SARIF à filtrer.")
    tr.add_argument("niveau", choices=sorted(NIVEAUX_ADMIS), help="Niveau à retenir.")

    args = parser.parse_args()
    json_flag = getattr(args, "json", False)

    # --------------------------------------------------------
    if args.commande == "emettre":
        verdicts = []
        if args.verdicts:
            verdicts = [
                Verdict(regle=v[0], niveau=v[1], message=v[2], fichier=v[3] or None)
                for v in args.verdicts
            ]
        sarif = emettre(args.outil, args.version, verdicts, args.racine)
        sarif["denominateur"] = len(verdicts)
        if sarif["denominateur"] == 0:
            sys.stderr.write("denominateur nul : aucun verdict examine.\n")
            if json_flag:
                json.dump(sarif, sys.stdout, ensure_ascii=False, indent=2)
                sys.stdout.write("\n")
            return 3
        if json_flag:
            json.dump(sarif, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
            return 0
        sys.stdout.write(_affichage_humain(sarif) + "\n")
        return 0

    # --------------------------------------------------------
    if args.commande == "rassembler":
        try:
            sarif = rassembler(args.sarifs, args.racine)
        except Exception as exc:
            sys.stderr.write(f"Erreur lors de la fusion : {exc}\n")
            if json_flag:
                json.dump({"erreur": str(exc), "denominateur": 0}, sys.stdout, ensure_ascii=False, indent=2)
                sys.stdout.write("\n")
            return 1

        sarif["denominateur"] = len(args.sarifs)
        if sarif["denominateur"] == 0:
            sys.stderr.write("denominateur nul : aucun fichier SARIF examine.\n")
            if json_flag:
                json.dump(sarif, sys.stdout, ensure_ascii=False, indent=2)
                sys.stdout.write("\n")
            return 3

        if args.trier:
            sarif = trier(sarif, args.trier)

        if json_flag:
            json.dump(sarif, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(_affichage_humain(sarif) + "\n")

        return 1 if _compter_niveaux(sarif).get("error", 0) else 0

    # --------------------------------------------------------
    if args.commande == "trier":
        try:
            sarif = charger_sarif(args.sarif)
        except Exception as exc:
            sys.stderr.write(f"Erreur de lecture : {exc}\n")
            if json_flag:
                json.dump({"erreur": str(exc), "denominateur": 0}, sys.stdout, ensure_ascii=False, indent=2)
                sys.stdout.write("\n")
            return 1

        sarif_filtré = trier(sarif, args.niveau)
        sarif_filtré["denominateur"] = 1
        nb_err = _compter_niveaux(sarif_filtré).get("error", 0)

        if json_flag:
            json.dump(sarif_filtré, sys.stdout, ensure_ascii=False, indent=2)
            sys.stdout.write("\n")
        else:
            sys.stdout.write(_affichage_humain(sarif_filtré) + "\n")

        return 1 if nb_err else 0

    return 0

if __name__ == "__main__":
    raise SystemExit(main())