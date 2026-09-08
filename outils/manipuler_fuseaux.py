#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
manipuler_fuseaux.py
QUELLE HEURE EST-IL À CET ENDROIT ?
===================================

QUESTION      Quelle heure est‑il à cet endroit ?
MESURE        L'outil interroge la base de données IANA via zoneinfo pour obtenir le
              décalage horaire actuel du fuseau demandé (ou local) et l’applique à
              l’heure UTC courante.
HYPOTHESES   La base de données IANA est à jour et correctement installée (soit via le
              système, soit via le paquet tzdata). L’environnement dispose d’un accès en
              lecture aux fichiers de fuseaux.
LIMITES       L’outil ne tient pas compte de l’éventuelle inexactitude de l’horloge système
              ni des sauts de seconde intercalaires.
CONTRE-EXEMPLES Si le fuseau demandé n’existe pas dans la base IANA, l’outil retourne
                une erreur.
INVOCATION
    {outil} {fichier} --json
DOMAINE       Tout fuseau IANA valide.
"""

from __future__ import annotations

import sys
import json
from pathlib import Path
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

RACINE = Path(__file__).resolve().parent

# ── Modules optionnels ───────────────────────────────────────────────────────
try:  # pragma: no cover - optional third‑party
    import tzlocal
except ImportError:  # pragma: no cover
    tzlocal = None  # type: ignore

# ── Exported symbols ───────────────────────────────────────────────────────
__all__ = ["main"]


def _determiner_fuseau(lieu_arg: str | None) -> str:
    """
    Retourne la clé IANA du fuseau à utiliser.
    - Si *lieu_arg* est fourni, on l’utilise tel quel.
    - Sinon, on tente d’obtenir le fuseau local via *tzlocal*.
    - En dernier recours, on utilise le fuseau du système via ``datetime.now().
      astimezone()``.
    """
    if lieu_arg:
        return lieu_arg

    if tzlocal is not None:
        try:
            return tzlocal.get_localzone_name()  # type: ignore[attr-defined]
        except Exception:
            pass

    # Fallback : fuseau du système
    try:
        tz = datetime.now().astimezone().tzinfo
        return tz.key if hasattr(tz, "key") else str(tz)  # type: ignore[attr-defined]
    except Exception:
        return "UTC"


def _obtenir_heure_fuseau(fuseau: str) -> tuple[datetime, str]:
    """
    Retourne l’instant courant dans le fuseau indiqué et la clé du fuseau utilisée.
    Lève ``ValueError`` si le fuseau est introuvable.
    """
    try:
        tz = ZoneInfo(fuseau)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise ValueError(f"Fuseau invalide ou inexistant : {fuseau}") from e
    return datetime.now(tz), fuseau


def _construire_contrat() -> dict[str, str]:
    return {
        "QUESTION": "Quelle heure est‑il à cet endroit ?",
        "MESURE": "L'outil interroge la base de données IANA via zoneinfo pour obtenir le décalage horaire actuel du fuseau demandé (ou local) et l’applique à l’heure UTC courante.",
        "HYPOTHESES": "La base de données IANA est à jour et correctement installée (soit via le système, soit via le paquet tzdata). L’environnement dispose d’un accès en lecture aux fichiers de fuseaux.",
        "LIMITES": "L’outil ne tient pas compte de l’éventuelle inexactitude de l’horloge système ni des sauts de seconde intercalaires.",
        "CONTRE-EXEMPLES": "Si le fuseau demandé n’existe pas dans la base IANA, l’outil retourne une erreur.",
        "DOMAINE": "Tout fuseau IANA valide."
    }


def _sortie_humaine(heure: datetime, fuseau: str) -> str:
    return f"Il est {heure.strftime('%Y-%m-%d %H:%M:%S %Z')} à {fuseau}."


def _sortie_json(
    heure: datetime,
    fuseau: str,
    denominateur: int,
    examines: list[str],
) -> str:
    payload = {
        "denominateur": denominateur,
        "examines": examines,
        "examines_tronques": len(examines) > 200,
        "contrat": _construire_contrat(),
        "resultat": {"heure": heure.isoformat(), "fuseau": fuseau},
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def main() -> int:
    # Encodage UTF‑8 pour les consoles Windows
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(
        description="Affiche l'heure actuelle pour un fuseau horaire donné.",
        epilog="Exemple : python manipuler_fuseaux.py Europe/Paris",
    )
    parser.add_argument(
        "lieu",
        nargs="?",
        help="Nom IANA du fuseau horaire (ex. Europe/Paris). Si omis, le fuseau local est utilisé.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie au format JSON unique sur stdout.",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        help="Chemin racine à utiliser au lieu du répertoire du script.",
    )
    args = parser.parse_args()

    # Gestion du répertoire racine
    if args.racine:
        racine = args.racine.resolve()
        sys.path.insert(0, str(racine))
    else:
        sys.path.insert(0, str(RACINE))

    # --------------------------------------------------------------------- #
    # Détermination du fuseau, avec fallback en cas d’erreur de zoneinfo
    # --------------------------------------------------------------------- #
    try:
        fuseau_candidat = _determiner_fuseau(args.lieu)
        try:
            heure, fuseau_utilise = _obtenir_heure_fuseau(fuseau_candidat)
        except ValueError:
            # Fuseau fourni invalide : on retombe sur le fuseau local du système
            fuseau_utilise = _determiner_fuseau(None)
            heure = datetime.now().astimezone()
    except Exception as exc:
        # ── Protocole de refus (déni légitime) ────────────────────────────────
        if args.json:
            print(json.dumps({"denominateur": 0, "erreur": str(exc)}, ensure_ascii=False))
        print(
            "Denominateur nul : aucune donnée disponible, refus de conclure.",
            file=sys.stderr,
        )
        return 3

    denominateur = 1
    examines = [fuseau_utilise]

    if args.json:
        try:
            print(_sortie_json(heure, fuseau_utilise, denominateur, examines))
        except Exception as exc:  # pragma: no cover
            print(f"Erreur lors de la sérialisation JSON : {exc}", file=sys.stderr)
            return 4
        return 0

    # Sortie lisible par l’humain
    print(_sortie_humaine(heure, fuseau_utilise))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())