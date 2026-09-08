"""
QUESTION      Puis-je générer des nombres aléatoires sans `random` ?
MESURE        Génération effective de nombres pseudo-aléatoires via `hashlib`.
HYPOTHÈSES    L'interpréteur exécute Python 3.14 et `hashlib` est disponible.
LIMITES       Les nombres générés ne sont pas cryptographiquement sûrs. Python pur ne peut pas fournir de tels nombres sans `secrets` ou `os.urandom`.
CONTRE-EXEMPLES Si la graine est constante et connue, la séquence est prédictible.
DOMAINE       Scripts nécessitant un pseudo-aléa simple sans dépendre du module `random`.
"""
from __future__ import annotations

import sys
import json
import argparse
import hashlib
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

__all__ = ["generer_nombres_aleatoires", "main"]


def generer_nombres_aleatoires(nombre: int, graine: bytes | None = None) -> list[int]:
    """Génère une liste de nombres pseudo-aléatoires en utilisant hashlib."""
    if graine is None:
        graine = str(time.time_ns()).encode('utf-8')
    nombres: list[int] = []
    h = hashlib.sha256()
    h.update(graine)
    for i in range(nombre):
        h.update(str(i).encode('utf-8'))
        digest = h.digest()
        val = int.from_bytes(digest[:8], 'big')
        nombres.append(val)
    return nombres


def _contrat() -> dict[str, str]:
    return {
        "QUESTION": "Puis-je générer des nombres aléatoires sans `random` ?",
        "MESURE": "Génération effective de nombres pseudo-aléatoires via `hashlib`.",
        "HYPOTHESES": "L'interpréteur exécute Python 3.14 et `hashlib` est disponible.",
        "LIMITES": "Les nombres générés ne sont pas cryptographiquement sûrs. Python pur ne peut pas fournir de tels nombres sans `secrets` ou `os.urandom`.",
        "CONTRE-EXEMPLES": "Si la graine est constante et connue, la séquence est prédictible.",
        "DOMAINE": "Scripts nécessitant un pseudo-aléa simple sans dépendre du module `random`.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Génère des nombres aléatoires sans le module `random` en utilisant `hashlib`.",
        epilog="Exemple d'appel réel : python generateur_aleatoire.py --nombre 5 --json"
    )
    parser.add_argument("--nombre", type=int, default=10, help="Nombre de nombres aléatoires à générer.")
    parser.add_argument("--json", action="store_true", help="Rend un seul objet JSON sur stdout.")
    parser.add_argument("--racine", type=str, default=str(RACINE), help="Surcharge la racine et l'insère en tête de sys.path.")

    args = parser.parse_args()

    if args.racine:
        racine_path = Path(args.racine).resolve()
        if str(racine_path) not in sys.path:
            sys.path.insert(0, str(racine_path))

    if args.nombre < 0:
        err_msg = "Erreur : le nombre ne peut pas être négatif."
        if args.json:
            print(json.dumps({"erreur": err_msg}, ensure_ascii=False), file=sys.stdout)
        else:
            print(err_msg, file=sys.stderr)
        return 2

    if args.nombre == 0:
        err_msg = "Refus de conclure : le denominateur est nul (nombre=0)."
        if args.json:
            contrat = _contrat()
            resultat = {
                **contrat,
                "denominateur": 0,
                "examines": [],
                "examines_tronques": False,
            }
            print(json.dumps(resultat, ensure_ascii=False), file=sys.stdout)
        else:
            print(err_msg, file=sys.stderr)
        return 3

    try:
        nombres = generer_nombres_aleatoires(args.nombre)
    except Exception as e:
        err_msg = f"Erreur lors de la génération : {e}"
        if args.json:
            print(json.dumps({"erreur": err_msg}, ensure_ascii=False), file=sys.stdout)
        else:
            print(err_msg, file=sys.stderr)
        return 1

    contrat = _contrat()

    if args.json:
        examines = [{"index": i, "valeur": n} for i, n in enumerate(nombres[:200])]
        resultat = {
            **contrat,
            "denominateur": len(nombres),
            "examines": examines,
            "examines_tronques": len(nombres) > 200,
        }
        print(json.dumps(resultat, ensure_ascii=False), file=sys.stdout)
    else:
        print("Contrat de mesure :")
        for k, v in contrat.items():
            print(f"  {k} : {v}")
        print()
        print("Nombres générés :")
        for n in nombres:
            print(n)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())