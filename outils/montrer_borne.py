"""QUESTION       comment montrer cette structure sans saturer le lecteur ?
MESURE         reprlib borné, comparé à repr/json/pprint sur la même donnée
HYPOTHÈSES     ≈ 4 caractères par jeton — approximation DÉCLARÉE, non mesurée
               pour le modèle visé
LIMITES        les bornes reprlib sont PAR DIMENSION, pas globales ; la sortie
               n'est plus du JSON valide ; un objet à __repr__ coûteux ou
               récursif peut piéger
CONTRE-EXEMPLE maxdict=1000 rend 23 268 caractères — « borné » ne veut rien
               dire sans mesure de la sortie
INVOCATION
    {outil} --fichier {fichier} --json
DOMAINE        structures Python inspectables, destinées à être lues
"""
from __future__ import annotations

import sys
import json
import reprlib
import pprint
import textwrap
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

CONTRAT = {
    "QUESTION": "comment montrer cette structure sans saturer le lecteur ?",
    "MESURE": "reprlib borné, comparé à repr/json/pprint sur la même donnée",
    "HYPOTHÈSES": "≈ 4 caractères par jeton — approximation DÉCLARÉE, non mesurée pour le modèle visé",
    "LIMITES": "les bornes reprlib sont PAR DIMENSION, pas globales ; la sortie n'est plus du JSON valide ; un objet à __repr__ coûteux ou récursif peut piéger",
    "CONTRE-EXEMPLE": "maxdict=1000 rend 23 268 caractères — « borné » ne veut rien dire sans mesure de la sortie",
    "DOMAINE": "structures Python inspectables, destinées à être lues"
}

def base_json(denominateur: int) -> dict:
    return {
        "denominateur": denominateur,
        "contrat": CONTRAT,
        "examines": []
    }

def eprouver_tiktoken() -> tuple[bool, str, Any]:
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        enc.encode("test")
        return True, "tiktoken", enc
    except Exception:
        return False, "approximation", None

def eprouver_wcwidth() -> tuple[bool, str, Any]:
    try:
        import wcwidth
        wcwidth.wcswidth("abc")
        return True, "wcwidth", wcwidth.wcswidth
    except ImportError:
        return False, "len", len

def montrer(objet: Any, jetons: int = 200, enc: Any = None) -> tuple[str, int, int, int, int]:
    r = reprlib.Repr()
    r.maxlevel = 5
    r.maxdict = 10
    r.maxlist = 10
    r.maxstring = 50
    r.maxother = 50

    try:
        texte_repr = r.repr(objet)
    except RecursionError:
        texte_repr = "<objet avec __repr__ récursif>"

    taille_avant = len(texte_repr)
    jetons_avant = len(enc.encode(texte_repr)) if enc else taille_avant // 4

    if enc:
        try:
            budget_tokens = jetons
            texte_borne = r.repr(objet)
            while True:
                encoded = enc.encode(texte_borne)
                if len(encoded) <= budget_tokens:
                    break
                texte_borne = texte_borne[:-1]
            texte_borne += f"... [coupé à {budget_tokens} jetons ; {len(encoded)} jetons auraient suffi]"
        except Exception:
            budget_caracteres = jetons * 4
            texte_borne = r.repr(objet)
            orig_len = len(texte_borne)
            if orig_len > budget_caracteres:
                texte_borne = texte_borne[:budget_caracteres] + f"... [coupé à {budget_caracteres} caractères ; {orig_len} auraient suffi]"
    else:
        budget_caracteres = jetons * 4
        texte_borne = r.repr(objet)
        orig_len = len(texte_borne)
        if orig_len > budget_caracteres:
            texte_borne = texte_borne[:budget_caracteres] + f"... [coupé à {budget_caracteres} caractères ; {orig_len} auraient suffi]"

    taille_apres = len(texte_borne)
    if enc:
        try:
            jetons_apres = len(enc.encode(texte_borne))
        except Exception:
            jetons_apres = taille_apres // 4
    else:
        jetons_apres = taille_apres // 4

    return texte_borne, taille_avant, taille_apres, jetons_avant, jetons_apres

def structure(objet: Any) -> str:
    if isinstance(objet, dict):
        if not objet:
            return "{}"
        items = []
        for k, v in objet.items():
            items.append(f"{k}: {structure(v)}")
        return f"dict[{len(objet)}] {{ {', '.join(items)} }}"
    elif isinstance(objet, list):
        if not objet:
            return "list[0]"
        return f"list[{len(objet)}] of {structure(objet[0])}"
    elif isinstance(objet, str):
        return f"str[{len(objet)}]"
    elif isinstance(objet, (int, float)):
        return type(objet).__name__
    else:
        return type(objet).__name__

def comparer(objet: Any) -> dict:
    r = reprlib.Repr()
    r.maxlevel = 2
    try:
        repr_len = len(r.repr(objet))
    except RecursionError:
        repr_len = len("<objet avec __repr__ récursif>")
    return {
        "repr": len(repr(objet)),
        "json": len(json.dumps(objet, default=str)),
        "pprint": len(pprint.pformat(objet, depth=2, sort_dicts=False)),
        "reprlib": repr_len
    }

def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="Montre une structure bornée pour économiser des jetons.",
        epilog="Exemple: python montrer_borne.py --fichier data.json --jetons 100"
    )
    parser.add_argument("--racine", type=str, help="Surcharge la racine du projet")
    parser.add_argument("--json", action="store_true", help="Sortie JSON")
    parser.add_argument("--jetons", type=int, default=200, help="Budget en jetons")
    parser.add_argument("--structure", action="store_true", help="Afficher le squelette")
    parser.add_argument("--comparer", action="store_true", help="Comparer les formes")
    parser.add_argument("--fichier", type=str, help="Chemin vers un fichier JSON ou Python contenant l'objet")

    args = parser.parse_args()

    racine = Path(args.racine).resolve() if args.racine else RACINE

    # Cas de refus légitime
    if not args.fichier and not sys.stdin.read():
        if args.json:
            print(json.dumps(base_json(0), ensure_ascii=False))
        print("Denominateur nul : rien à examiner, refus de conclure.", file=sys.stderr)
        return 3

    try:
        if args.fichier:
            chemin = racine / args.fichier if not Path(args.fichier).is_absolute() else Path(args.fichier)
            if args.fichier.endswith(".py"):
                with open(chemin, 'r', encoding='utf-8') as f:
                    source = f.read()
                compile(source, str(chemin), "exec")
                ns: dict[str, Any] = {}
                exec(source, ns)
                objet = ns.get("OBJET")
                if objet is None:
                    raise ValueError("OBJET non défini dans le fichier Python")
            else:
                with open(chemin, 'r', encoding='utf-8') as f:
                    objet = json.load(f)
        else:
            contenu = sys.stdin.read()
            if not contenu:
                raise ValueError("aucun objet fourni")
            objet = json.loads(contenu)
    except Exception as e:
        sortie = base_json(0)
        sortie["erreur"] = str(e)
        if args.json:
            print(json.dumps(sortie, ensure_ascii=False))
        else:
            print(f"Erreur de lecture: {e}", file=sys.stderr)
        return 1

    if args.jetons <= 0:
        sortie = base_json(0)
        sortie["erreur"] = "budget en jetons doit être > 0"
        if args.json:
            print(json.dumps(sortie, ensure_ascii=False))
        else:
            print("Erreur: budget en jetons doit être > 0", file=sys.stderr)
        return 2

    tiktoken_ok, tiktoken_voie, enc = eprouver_tiktoken()
    try:
        wcwidth_ok, wcwidth_voie, wcswidth = eprouver_wcwidth()
    except ImportError as e:
        sortie = base_json(0)
        sortie["erreur"] = str(e)
        if args.json:
            print(json.dumps(sortie, ensure_ascii=False))
        else:
            print(f"Erreur: {e}", file=sys.stderr)
        return 1

    if args.structure:
        res = structure(objet)
        sortie = base_json(len(repr(objet)))
        sortie["structure"] = res
        if args.json:
            print(json.dumps(sortie, ensure_ascii=False, indent=2))
        else:
            print(res)
            for k, v in CONTRAT.items():
                print(f"  {k}: {v}", file=sys.stderr)
        return 0

    if args.comparer:
        res = comparer(objet)
        sortie = base_json(len(repr(objet)))
        sortie["comparaison"] = res
        if args.json:
            print(json.dumps(sortie, ensure_ascii=False, indent=2))
        else:
            for k, v in res.items():
                print(f"{k}: {v}")
            for k, v in CONTRAT.items():
                print(f"  {k}: {v}", file=sys.stderr)
        return 0

    texte, avant, apres, jetons_avant, jetons_apres = montrer(objet, args.jetons, enc)

    if avant == 0:
        sortie = base_json(0)
        sortie["erreur"] = "Dénominateur (taille avant) = 0, impossible de conclure"
        if args.json:
            print(json.dumps(sortie, ensure_ascii=False))
        else:
            print("Dénominateur (taille avant) = 0, impossible de conclure", file=sys.stderr)
        return 3

    largeur = wcswidth(texte) if wcwidth_ok else len(texte)
    largeur_str = str(largeur) if largeur != -1 else "indéterminé"

    sortie = base_json(avant)
    sortie.update({
        "texte": texte,
        "taille_avant": avant,
        "taille_apres": apres,
        "jetons_avant": jetons_avant,
        "jetons_apres": jetons_apres,
        "largeur_colonnes": largeur_str,
        "voie_jetons": tiktoken_voie,
        "voie_colonnes": wcwidth_voie,
    })

    if args.json:
        print(json.dumps(sortie, ensure_ascii=False, indent=2))
    else:
        print(texte)
        ratio = avant // apres if apres else "N/A"
        print(f"\n{jetons_avant} jetons -> {jetons_apres} jetons   (×{ratio})", file=sys.stderr)
        print(f"{avant} caractères -> {apres} caractères", file=sys.stderr)
        print(f"Largeur: {largeur_str} colonnes", file=sys.stderr)
        print(f"Voie jetons: {tiktoken_voie}", file=sys.stderr)
        print(f"Voie colonnes: {wcwidth_voie}", file=sys.stderr)
        print("Contrat:", file=sys.stderr)
        for k, v in CONTRAT.items():
            print(f"  {k}: {v}", file=sys.stderr)

    return 0

if __name__ == "__main__":
    raise SystemExit(main())