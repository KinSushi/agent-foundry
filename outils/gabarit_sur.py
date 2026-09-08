"""gabarit_sur.py – Outil d'inspection et de rendu sécurisé des t‑strings (PEP 750)

QUESTION
    Ce texte peut‑il être construit sans risque d'injection ?
MESURE
    t‑strings (PEP 750) : la Template porte les parties statiques et les
    interpolations séparément, jamais déjà concaténées.
HYPOTHÈSES
    Python ≥ 3.14 ; le consommateur du gabarit sait traiter une Template.
LIMITES
    Ne protège que ce qui passe par le gabarit ; une chaîne assemblée ailleurs
    puis injectée échappe à tout contrôle. Ne remplace pas les paramètres liés
    d'un pilote SQL.
CONTRE‑EXEMPLE
    Une f‑string à la même apparence ne donne AUCUNE séparation – la ressemblance
    syntaxique est le piège principal.
DOMAINE
    Construction de texte en Python 3.14+.
"""

from __future__ import annotations

import json
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Union

# ------------------------------------------------------------
# Vérification de la version de Python
# ------------------------------------------------------------
if sys.version_info < (3, 14):
    sys.stderr.write(
        "Cet outil nécessite Python 3.14 ou supérieur (t‑strings, PEP 750).\n"
    )
    raise SystemExit(1)

# ------------------------------------------------------------
# Encodage de la sortie
# ------------------------------------------------------------
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# ------------------------------------------------------------
# Importations de la stdlib (optionnelles protégées)
# ------------------------------------------------------------
try:
    from string.templatelib import Template, convert  # type: ignore
except Exception as exc:  # pragma: no cover
    sys.stderr.write(f"Erreur d'importation de string.templatelib : {exc}\n")
    raise SystemExit(1)

# ------------------------------------------------------------
# Types
# ------------------------------------------------------------
InvisibleInfo = Dict[str, Any]
RenderResult = Union[str, List[str]]

# ------------------------------------------------------------
# Fonctions utilitaires du cœur (sans I/O)
# ------------------------------------------------------------
def _est_invisible(ch: str) -> bool:
    """Retourne True si le caractère est invisible selon la spécification."""
    cat = unicodedata.category(ch)
    if cat in {"Cf", "Cc"}:
        return True
    if cat == "Zs" and ch != " ":
        return True
    return False


def trouver_invisibles(val: str) -> List[InvisibleInfo]:
    """Renvoie la liste des caractères invisibles présents dans *val*."""
    result: List[InvisibleInfo] = []
    for idx, ch in enumerate(val):
        if _est_invisible(ch):
            code = f"U+{ord(ch):04X}"
            name = unicodedata.name(ch, "(sans nom)")
            result.append({"position": idx, "codepoint": code, "nom": name})
    return result


def _valeur_formatee(interp: Any) -> str:
    """Applique conversion et format_spec à une interpolation, renvoie la chaîne."""
    val = convert(interp.value, interp.conversion)
    if interp.format_spec:
        return format(val, interp.format_spec)
    return str(val)


def argv(t: Template) -> List[str]:
    """Renvoie une liste d'arguments pour subprocess, sans shell.

    Intercale les parties fixes non vides et les valeurs interpolées.
    Ne produit jamais une chaîne de commande shell.
    """
    assert len(t.strings) == len(t.interpolations) + 1
    result: List[str] = []
    for i, s in enumerate(t.strings):
        result.append(s)
        if i < len(t.interpolations):
            valeur = _valeur_formatee(t.interpolations[i])
            invisibles = trouver_invisibles(valeur)
            if invisibles:
                details = ", ".join(
                    f"{info['codepoint']} ({info['nom']}) à la position {info['position']}"
                    for info in invisibles
                )
                raise ValueError(
                    f"Valeur refusée (caractère invisible détecté) : {details}"
                )
            result.append(valeur)
    return result


def rendre(t: Template, politique: str = "texte") -> RenderResult:
    """Rend le gabarit *t* selon la *politique* demandée.

    Politiques :
        texte   – rend, refuse toute valeur contenant un invisible.
        journal – tronque à N caractères, échappe les sauts de ligne.
        argv    – renvoie une liste d'arguments (délègue à argv()).
    """
    assert len(t.strings) == len(t.interpolations) + 1

    if politique == "argv":
        return argv(t)

    if politique not in {"texte", "journal"}:
        raise ValueError(f"Politique inconnue : {politique}")

    if politique == "texte":
        parts = [t.strings[0]]
        for i, interp in enumerate(t.interpolations):
            s = _valeur_formatee(interp)
            invisibles = trouver_invisibles(s)
            if invisibles:
                details = ", ".join(
                    f"{info['codepoint']} ({info['nom']}) à la position {info['position']}"
                    for info in invisibles
                )
                raise ValueError(
                    f"Valeur refusée (caractère invisible détecté) : {details}"
                )
            parts.append(s)
            parts.append(t.strings[i + 1])
        return "".join(parts)

    # politique == "journal"
    MAX_JOURNAL = 200
    parts = [t.strings[0]]
    for i, interp in enumerate(t.interpolations):
        s = _valeur_formatee(interp)
        s = s.replace("\n", "\\n").replace("\r", "\\r")
        if len(s) > MAX_JOURNAL:
            s = s[:MAX_JOURNAL] + "…"
        parts.append(s)
        parts.append(t.strings[i + 1])
    return "".join(parts)


def inspecter(t: Template) -> dict:
    """Inspecte le gabarit *t* sans le rendre.

    Renvoie un dict contenant les métadonnées du gabarit.
    """
    assert len(t.strings) == len(t.interpolations) + 1

    strings = list(t.strings)
    expressions: List[str] = []
    valeurs: List[Any] = []
    conversions: List[Any] = []
    formats: List[str] = []
    invisibles: List[List[InvisibleInfo]] = []

    # Invisibles dans les parties fixes
    fixed_invisibles: List[InvisibleInfo] = []
    for i, s in enumerate(t.strings):
        local_invisibles = trouver_invisibles(s)
        for info in local_invisibles:
            info["position_globale"] = sum(len(t.strings[j]) for j in range(i)) + info["position"]
            info["type"] = "fixe"
            fixed_invisibles.append(info)

    # Valeurs formatées
    formatted_values = [_valeur_formatee(interp) for interp in t.interpolations]

    # Offsets globaux
    offsets: List[int] = []
    offset = 0
    for i in range(len(t.interpolations)):
        offset += len(t.strings[i])
        offsets.append(offset)
        offset += len(formatted_values[i])

    for i, interp in enumerate(t.interpolations):
        expressions.append(interp.expression)
        valeurs.append(interp.value)
        conversions.append(interp.conversion)
        formats.append(interp.format_spec)
        s = formatted_values[i]
        local_invisibles = trouver_invisibles(s)
        for info in local_invisibles:
            info["position_globale"] = offsets[i] + info["position"]
            info["type"] = "interpolation"
        invisibles.append(local_invisibles)

    return {
        "strings": strings,
        "interpolations": list(t.interpolations),
        "expressions": expressions,
        "valeurs": valeurs,
        "conversions": conversions,
        "formats": formats,
        "invisibles": invisibles,
        "invisibles_fixes": fixed_invisibles,
    }


def verifier_fichier(chemin: Path) -> List[InvisibleInfo]:
    """Analyse *chemin* et renvoie la liste des caractères invisibles rencontrés."""
    texte = chemin.read_text(encoding="utf-8", errors="strict")
    result: List[InvisibleInfo] = []
    for ligne_num, ligne in enumerate(texte.splitlines(), start=1):
        for info in trouver_invisibles(ligne):
            info["ligne"] = ligne_num
            result.append(info)
    return result


def _selftest() -> dict:
    """Test interne qui appelle rendre et inspecter sur un gabarit simple."""
    code = 't"bonjour {nom}"'
    ns: Dict[str, Any] = {"nom": "test"}
    t = eval(compile(code, "<selftest>", "eval"), ns)
    r = rendre(t, politique="texte")
    i = inspecter(t)
    return {"rendre": r, "inspecter": i}


# ------------------------------------------------------------
# Contrat de mesure
# ------------------------------------------------------------
def _contrat() -> Dict[str, str]:
    """Renvoie le contrat de mesure sous forme de dictionnaire."""
    return {
        "QUESTION": "Ce texte peut-il etre construit sans risque d'injection ?",
        "MESURE": "t-strings (PEP 750) : la Template porte les parties statiques et les interpolations SEPAREMENT, jamais deja concatenees",
        "HYPOTHÈSES": "Python >= 3.14 ; le consommateur du gabarit sait traiter une Template",
        "LIMITES": "ne protege que ce qui passe PAR le gabarit ; une chaine assemblee ailleurs puis injectee echappe a tout controle ; ne remplace pas les parametres lies d'un pilote SQL",
        "CONTRE-EXEMPLE": "une f-string a la meme apparence et ne donne AUCUNE separation -- la ressemblance syntaxique est le piege principal",
        "DOMAINE": "construction de texte en Python 3.14+",
    }


# ------------------------------------------------------------
# Affichage / sortie
# ------------------------------------------------------------
def _afficher_humain(
    result: Any,
    json_mode: bool,
    denominateur: int,
    examines: List[str],
) -> None:
    """Écrit *result* sur stdout selon le mode demandé, en ajoutant le dénominateur
    et la liste des éléments examinés."""
    if json_mode:
        payload = {
            "resultat": result,
            "contrat": _contrat(),
            "denominateur": denominateur,
            "examines": examines[:200],
            "examines_tronques": max(0, len(examines) - 200),
        }
        json.dump(payload, sys.stdout, ensure_ascii=False, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        # Affichage humain : résultat + dénominateur + éléments (troncés)
        sys.stdout.write(str(result) + "\n")
        sys.stdout.write(f"Dénominateur : {denominateur}\n")
        if examines:
            displayed = examines[:200]
            sys.stdout.write("Éléments examinés : " + ", ".join(displayed) + "\n")
            if len(examines) > 200:
                sys.stdout.write(
                    f"... et {len(examines) - 200} éléments supplémentaires tronqués.\n"
                )


# ------------------------------------------------------------
# Interface en ligne de commande
# ------------------------------------------------------------
def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="gabarit_sur.py",
        description="Inspection et rendu sécurisé des t‑strings (PEP 750).",
        epilog="Exemple : python gabarit_sur.py verifier mon_fichier.py --json",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Chemin racine à utiliser à la place de celui dérivé du script.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Produire la sortie au format JSON unique.",
    )
    subparsers = parser.add_subparsers(dest="commande", required=True)

    verifier_parser = subparsers.add_parser(
        "verifier",
        help="Analyse un fichier texte et signale les caractères invisibles.",
    )
    verifier_parser.add_argument(
        "fichier",
        type=Path,
        help="Chemin du fichier à analyser.",
    )

    subparsers.add_parser(
        "selftest",
        help="Test interne : appelle rendre et inspecter sur un gabarit simple.",
    )

    args = parser.parse_args()

    # --------------------------------------------------------
    # Gestion de la sous‑commande « verifier »
    # --------------------------------------------------------
    if args.commande == "verifier":
        fichier: Path = args.fichier
        if not fichier.is_file():
            sys.stderr.write(f"Fichier introuvable : {fichier}\n")
            return 2

        try:
            invisibles = verifier_fichier(fichier)
        except UnicodeDecodeError as e:
            sys.stderr.write(f"Erreur de décodage du fichier : {e}\n")
            return 2

        # Dénominateur = nombre de lignes parcourues
        texte = fichier.read_text(encoding="utf-8", errors="strict")
        denominateur = len(texte.splitlines())
        examines = [str(fichier)]

        if denominateur == 0:
            sys.stderr.write("Zéro élément examiné\n")
            return 3

        if not invisibles:
            _afficher_humain(
                "Aucun caractère invisible détecté.", args.json, denominateur, examines
            )
            return 0

        lignes = [
            f"Ligne {info['ligne']} : {info['codepoint']} – {info['nom']}"
            for info in invisibles
        ]
        _afficher_humain("\n".join(lignes), args.json, denominateur, examines)
        return 1

    # --------------------------------------------------------
    # Gestion de la sous‑commande « selftest »
    # --------------------------------------------------------
    if args.commande == "selftest":
        resultat = _selftest()
        # Le selftest examine un unique template → dénominateur = 1
        denominateur = 1
        examines = ["template"]
        if denominateur == 0:  # pragma: no cover – impossible ici
            sys.stderr.write("Zéro élément examiné\n")
            return 3
        _afficher_humain(resultat, args.json, denominateur, examines)
        return 0

    sys.stderr.write("Commande inconnue.\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())