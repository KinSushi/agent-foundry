"""Écrire un texte en ASCII sûr pour une URL ou un nom de fichier, sans perte muette ni collision.

Mesuré dans cette session : la recette courante unicodedata.normalize("NFKD") puis
encode("ascii", "ignore") transforme « Łódź Straße Ærøskøbing Œuvre Đorđe þing Москва Αθήνα »
(52 caractères) en « odz Strae rskbing uvre ore ing » suivi de deux espaces (32 caractères) :
Ł, ß, Æ, ø, Œ, Đ, đ, þ et tout le cyrillique et le grec disparaissent sans un mot.

QUESTION
    Comment écrire ce texte en ASCII sûr pour une URL (slug) ou un nom de fichier valable
    sous Windows, macOS et Linux, et quelles entrées d'une liste entrent alors en collision ?
MESURE
    Translittération en trois temps : table de l'outil (ß, æ, œ, ø, ł, đ, ð, þ, ı, ħ, ŋ,
    cyrillique russe, ukrainien, biélorusse et serbe, grec de base, ponctuation typographique),
    décomposition NFKD avec retrait des diacritiques, puis la table encore ; tout caractère
    restant hors ASCII est compté comme perdu et nommé (point de code). Slug : minuscules,
    suite de caractères hors [a-z0-9] remplacée par le séparateur, longueur maximale coupée
    à une frontière de mot. Nom de fichier : caractères < > : " / \\ | ? * et de contrôle
    remplacés, points et espaces finaux retirés, noms réservés de Windows (con, prn, aux,
    nul, com0 à com9, lpt0 à lpt9, avec ou sans extension) suffixés, 255 octets au plus en
    gardant l'extension. Collisions : slugs identiques et noms de fichier égaux à la casse
    près (systèmes insensibles à la casse) entre entrées distinctes, avec une version rendue
    unique (-2, -3… ; _2, _3…). Comparaison facultative avec unidecode, python-slugify et
    pathvalidate.
HYPOTHÈSES
    Le texte est de l'Unicode valide (un octet invalide reçu en argument devient le caractère
    de remplacement et compte comme perdu) ; l'ASCII visé est l'alphabet latin de base. La table suit l'usage
    courant (proche de unidecode pour le cyrillique, grec moderne : η en i, β en v), pas une
    norme nationale de translittération.
LIMITES
    Les écritures hors table (chinois, japonais, arabe, hébreu, indiennes…) ne sont pas
    translittérées : leurs caractères sont déclarés perdus et le slug peut être vide (sa
    version unique devient alors h suivi de 8 chiffres hexadécimaux d'une empreinte blake2s
    de l'entrée). Pas de règle contextuelle (ї ou є en début de mot, ё noté e, diphtongues
    grecques ου, αυ lettre à lettre). Les emoji sont perdus. La
    longueur d'un chemin complet (260 caractères sous Windows) n'est pas contrôlée.
CONTRE-EXEMPLES
    Constaté : « Ёжик » donne le slug « ezhik » ici comme avec python-slugify, mais
    « Iozhik » avec unidecode ; « Ελληνικά » donne « Ellinika » ici et « Ellenika » avec
    unidecode et python-slugify (η en e) : deux conventions, aucune n'est fausse, mais un
    slug déjà publié avec l'autre ne sera pas retrouvé. Pour com0 et lpt0, la liste officielle de Microsoft ne les cite
    pas : l'outil les suffixe quand même, par prudence (pathvalidate les refuse aussi).
INVOCATION
    {outil} "Łódź Straße" "Œuvre complète" "œuvre complète" con.txt --json
    {outil} --fichier {fichier} --json
DOMAINE
    Titres, noms de pièces jointes, identifiants d'URL et noms de fichiers produits à partir
    de texte libre, en particulier par lots où deux entrées peuvent donner le même nom.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

try:
    import unidecode
except ImportError:
    unidecode = None

try:
    import slugify as python_slugify
except ImportError:
    python_slugify = None

try:
    import pathvalidate
except ImportError:
    pathvalidate = None

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", INTITULE_HYPOTHESES, "LIMITES",
             "CONTRE-EXEMPLES", INTITULE_INVOCATION, "DOMAINE")

LATIN = {
    "ß": "ss", "ẞ": "SS", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ø": "o", "Ø": "O",
    "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ð": "d", "Ð": "D", "þ": "th", "Þ": "Th",
    "ı": "i", "ħ": "h", "Ħ": "H", "ŧ": "t", "Ŧ": "T", "ŋ": "ng", "Ŋ": "Ng", "ĸ": "k",
    "ŀ": "l", "Ŀ": "L", "ſ": "s",
}
CYRILLIQUE = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "iu",
    "я": "ia", "є": "ie", "і": "i", "ї": "i", "ґ": "g", "ў": "u", "ђ": "dj", "ј": "j",
    "љ": "lj", "њ": "nj", "ћ": "c", "џ": "dz", "ѓ": "g", "ѕ": "dz", "ќ": "k",
}
GREC = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th",
    "ι": "i", "κ": "k", "λ": "l", "μ": "m", "ν": "n", "ξ": "x", "ο": "o", "π": "p",
    "ρ": "r", "σ": "s", "ς": "s", "τ": "t", "υ": "y", "φ": "f", "χ": "ch", "ψ": "ps",
    "ω": "o",
}
PONCTUATION = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "“": '"', "”": '"', "„": '"',
    "‟": '"', "″": '"', "«": '"', "»": '"', "‹": "'", "›": "'", "‐": "-", "‑": "-",
    "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-", "⁄": "/", "•": "*", "·": ".",
    "€": "EUR", "©": "(c)", "®": "(r)", "×": "x", " ": " ",
}
RESERVES_WINDOWS = frozenset({"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(10)}
                             | {f"LPT{i}" for i in range(10)})
INTERDITS_FICHIER = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
NON_SLUG = re.compile(r"[^A-Za-z0-9]+")
EXTENSION = re.compile(r"^(.+?)(\.[A-Za-z0-9]{1,16})$")
SEPARATEURS = ("-", "_", ".")
PLAFOND_EXAMINES = 200
PLAFOND_DIVERGENCES = 50
NOM_OUTIL = "translitterer_slug"
CHAMP_ASCII = "ascii"
FORME_NFKD = "NFKD"
ENCODAGE = "utf-8"


def construire_table() -> dict[int, str]:
    """Table de translittération (majuscules dérivées des minuscules pour le cyrillique et le grec)."""
    table = dict(LATIN) | PONCTUATION
    for source in (CYRILLIQUE, GREC):
        for minuscule, ascii_ in source.items():
            table[minuscule] = ascii_
            majuscule = minuscule.upper()
            if len(majuscule) == 1 and majuscule != minuscule:
                table.setdefault(majuscule, ascii_[:1].upper() + ascii_[1:])
    return str.maketrans(table)


TABLE = construire_table()


class EntreeInvalide(Exception):
    """Fichier ou option inutilisable : code 2."""


@dataclass(frozen=True)
class Reglages:
    separateur: str
    max_longueur: int
    max_octets_nom: int
    minuscules: bool
    remplacement: str


def translitterer(texte: str) -> tuple[str, list[str]]:
    """ASCII obtenu et caractères perdus (ni dans la table, ni réductibles par NFKD)."""
    etape = unicodedata.normalize(FORME_NFKD, unicodedata.normalize("NFC", texte).translate(TABLE))
    etape = etape.translate(TABLE)
    sortie, perdus = [], []
    for caractere in etape:
        if caractere.isascii():
            sortie.append(" " if unicodedata.category(caractere) == "Cc" else caractere)
        elif unicodedata.category(caractere) != "Mn":
            perdus.append(caractere)
    return "".join(sortie), perdus


def decrire_perdus(perdus: list[str]) -> list[dict[str, str]]:
    """Caractères perdus distincts, avec point de code et nom Unicode."""
    return [{"caractere": c, "code": f"U+{ord(c):04X}", "nom": unicodedata.name(c, "?")}
            for c in dict.fromkeys(perdus)]


def couper_au_mot(texte: str, separateur: str, longueur: int) -> str:
    """Coupe à la dernière frontière de mot qui tient ; un seul mot trop long est coupé net."""
    if len(texte) <= longueur:
        return texte
    coupe = texte[:longueur + 1].rfind(separateur)
    return (texte[:coupe] if coupe > 0 else texte[:longueur]).strip(separateur)


def slugifier(ascii_texte: str, reglages: Reglages) -> str:
    """Slug : casse choisie, [A-Za-z0-9] seuls, séparateurs simples, longueur bornée."""
    texte = ascii_texte.lower() if reglages.minuscules else ascii_texte
    texte = NON_SLUG.sub(reglages.separateur, texte).strip(reglages.separateur)
    return couper_au_mot(texte, reglages.separateur, reglages.max_longueur)


def empreinte(entree: str) -> str:
    """Nom de repli déterministe pour une entrée sans aucun caractère translittérable."""
    return "h" + hashlib.blake2s(entree.encode(ENCODAGE), digest_size=4).hexdigest()


def nettoyer(texte: str) -> str:
    """Remplace par le caractère de remplacement les octets invalides reçus en argument."""
    try:
        brut = texte.encode(ENCODAGE, "surrogateescape")
    except UnicodeEncodeError:
        brut = texte.encode(ENCODAGE, "surrogatepass")
    return brut.decode(ENCODAGE, "replace")


def nom_de_fichier(ascii_texte: str, reglages: Reglages) -> tuple[str, list[str]]:
    """Nom valable sur les trois systèmes (vide si rien ne reste), et les corrections apportées."""
    notes = []
    nom = INTERDITS_FICHIER.sub(reglages.remplacement, ascii_texte)
    nom = " ".join(nom.split()).rstrip(". ")
    if nom != " ".join(ascii_texte.split()):
        notes.append("caractères interdits remplacés ou points/espaces finaux retirés")
    if not nom.strip("."):
        return "", notes + ["nom vide ou fait de points"]
    base, point, reste = nom.partition(".")
    if base.rstrip(" ").upper() in RESERVES_WINDOWS:
        nom = base.rstrip(" ") + reglages.remplacement + point + reste
        notes.append(f"nom réservé de Windows ({base.rstrip(' ').upper()}) suffixé")
    nom = borner_nom(nom, reglages.max_octets_nom)
    if nom.startswith("-"):
        notes.append("commence par un tiret : sera pris pour une option en ligne de commande")
    return nom, notes


def borner_nom(nom: str, max_octets: int) -> str:
    """Tronque à max_octets (ASCII : un caractère = un octet) en gardant l'extension."""
    if len(nom) <= max_octets:
        return nom
    trouve = EXTENSION.match(nom)
    extension = trouve.group(2) if trouve and len(trouve.group(2)) < max_octets else ""
    return nom[:max_octets - len(extension)].rstrip(". ") + extension


def suffixer(valeur: str, rang: int, reglages: Reglages, fichier: bool) -> str:
    """Version numérotée : slug-2 (dans la longueur maximale) ou nom_2.ext."""
    if not fichier:
        suffixe = f"{reglages.separateur}{rang}"
        return couper_au_mot(valeur, reglages.separateur, reglages.max_longueur - len(suffixe)) + suffixe
    trouve = EXTENSION.match(valeur)
    base, extension = (trouve.group(1), trouve.group(2)) if trouve else (valeur, "")
    return borner_nom(f"{base}{reglages.remplacement}{rang}{extension}", reglages.max_octets_nom)


def rendre_uniques(valeurs: list[str], entrees: list[str], cle: Any, reglages: Reglages,
                   fichier: bool) -> list[str]:
    """Premier arrivé garde sa valeur ; les suivants reçoivent le premier suffixe libre.

    Une valeur vide est remplacée par l'empreinte de son entrée.
    """
    pris: set[str] = set()
    sortie = []
    for valeur, entree in zip(valeurs, entrees):
        valeur = valeur or empreinte(entree)
        candidat, rang = valeur, 2
        while cle(candidat) in pris:
            candidat = suffixer(valeur, rang, reglages, fichier)
            rang += 1
        pris.add(cle(candidat))
        sortie.append(candidat)
    return sortie


def collisions(entrees: list[str], valeurs: list[str], cle: Any) -> list[dict[str, Any]]:
    """Groupes d'entrées distinctes qui donnent la même valeur (selon la clé)."""
    groupes: dict[str, list[int]] = defaultdict(list)
    for rang, valeur in enumerate(valeurs):
        if valeur:
            groupes[cle(valeur)].append(rang)
    sortie = []
    for valeur, rangs in groupes.items():
        distinctes = list(dict.fromkeys(entrees[r] for r in rangs))
        if len(distinctes) > 1:
            sortie.append({"valeur": valeur, "entrees": distinctes, "rangs": [r + 1 for r in rangs]})
    return sortie


def traiter(entrees: list[str], reglages: Reglages) -> dict[str, Any]:
    """Translittère, construit slugs et noms, détecte les collisions, propose des uniques."""
    lignes = []
    for entree in entrees:
        ascii_texte, perdus = translitterer(entree)
        nom, notes = nom_de_fichier(ascii_texte, reglages)
        lignes.append({"entree": entree, CHAMP_ASCII: ascii_texte, "perdus": decrire_perdus(perdus),
                       "slug": slugifier(ascii_texte, reglages), "nom_fichier": nom, "notes": notes})
    slugs = [ligne["slug"] for ligne in lignes]
    noms = [ligne["nom_fichier"] for ligne in lignes]
    for ligne, slug_u, nom_u in zip(lignes, rendre_uniques(slugs, entrees, str, reglages, False),
                                    rendre_uniques(noms, entrees, str.lower, reglages, True)):
        ligne["slug_unique"], ligne["nom_fichier_unique"] = slug_u, nom_u
    return {"lignes": lignes,
            "collisions_slug": collisions(entrees, slugs, str),
            "collisions_nom_fichier": collisions(entrees, noms, str.lower),
            "entrees_repetees": sorted({e for e in entrees if entrees.count(e) > 1})[:PLAFOND_EXAMINES]}


def comparer(lignes: list[dict[str, Any]], reglages: Reglages) -> dict[str, Any]:
    """Écarts avec les bibliothèques facultatives présentes (comparaison, jamais décision)."""
    comparaison: dict[str, Any] = {}
    if unidecode is not None:
        comparaison["unidecode"] = ecarts(lignes, CHAMP_ASCII, lambda e: unidecode.unidecode(e))
    if python_slugify is not None:
        comparaison["python-slugify"] = ecarts(lignes, "slug", lambda e: python_slugify.slugify(
            e, max_length=reglages.max_longueur, word_boundary=True,
            separator=reglages.separateur, lowercase=reglages.minuscules))
    if pathvalidate is not None:
        invalides = [ligne["nom_fichier_unique"] for ligne in lignes
                     if not pathvalidate.is_valid_filename(ligne["nom_fichier_unique"], platform="universal")]
        comparaison["pathvalidate"] = {"noms_juges_invalides": invalides[:PLAFOND_DIVERGENCES],
                                       "nombre": len(invalides)}
    return comparaison


def ecarts(lignes: list[dict[str, Any]], champ: str, autre: Any) -> dict[str, Any]:
    """Entrées pour lesquelles la bibliothèque rend autre chose que l'outil."""
    differences = []
    for ligne in lignes:
        valeur = autre(ligne["entree"])
        if valeur != ligne[champ]:
            differences.append({"entree": ligne["entree"], "outil": ligne[champ], "bibliotheque": valeur})
    return {"divergences": len(differences), "exemples": differences[:PLAFOND_DIVERGENCES]}


def lire_entrees_fichier(chemin: Path, plafond: int) -> tuple[list[str], int]:
    """Une entrée par ligne (UTF-8, marque d'ordre tolérée) ; lignes vides ignorées et comptées."""
    if not chemin.is_file():
        raise EntreeInvalide(f"fichier introuvable ou non ordinaire : {chemin}")
    if chemin.stat().st_size > plafond:
        raise EntreeInvalide(f"{chemin} dépasse {plafond} octets (--max-octets)")
    try:
        texte = chemin.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise EntreeInvalide(f"{chemin} n'est pas du texte UTF-8 (octet {exc.start})") from exc
    except OSError as exc:
        raise EntreeInvalide(f"lecture impossible de {chemin} : {exc.strerror or exc}") from exc
    if "\x00" in texte:
        raise EntreeInvalide(f"{chemin} contient des octets nuls : fichier binaire ?")
    lignes = texte.splitlines()
    gardees = [ligne.strip() for ligne in lignes if ligne.strip()]
    return gardees, len(lignes) - len(gardees)


def lire_contrat() -> dict[str, str]:
    """Sections du contrat de mesure, lues dans la docstring du module."""
    sections: dict[str, list[str]] = {}
    courant = None
    for ligne in (__doc__ or "").splitlines():
        if ligne.strip() in INTITULES and not ligne.startswith(" "):
            courant = ligne.strip()
            sections[courant] = []
        elif courant:
            sections[courant].append(ligne.strip())
    return {cle: " ".join(m for m in morceaux if m) for cle, morceaux in sections.items()}


def construire_rapport(resultat: dict[str, Any], ignorees: int, reglages: Reglages) -> dict[str, Any]:
    lignes = resultat["lignes"]
    avec_pertes = [ligne["entree"] for ligne in lignes if ligne["perdus"]]
    vides = [ligne["entree"] for ligne in lignes if not ligne["slug"] or not ligne["nom_fichier"]]
    return {
        "outil": NOM_OUTIL,
        "moteur": "stdlib",
        "comparaison": comparer(lignes, reglages),
        "denominateur": len(lignes),
        "examines": [ligne["entree"] for ligne in lignes][:PLAFOND_EXAMINES],
        "examines_tronques": len(lignes) > PLAFOND_EXAMINES,
        "lignes_vides_ignorees": ignorees,
        "entrees_avec_pertes": avec_pertes[:PLAFOND_EXAMINES],
        "resultats_vides": vides[:PLAFOND_EXAMINES],
        "collisions_slug": resultat["collisions_slug"],
        "collisions_nom_fichier": resultat["collisions_nom_fichier"],
        "entrees_repetees": resultat["entrees_repetees"],
        "reglages": {"separateur": reglages.separateur, "max_longueur": reglages.max_longueur,
                     "max_octets_nom": reglages.max_octets_nom, "minuscules": reglages.minuscules},
        "resultats": lignes,
        "contrat": lire_contrat(),
    }


def afficher_humain(rapport: dict[str, Any]) -> None:
    for ligne in rapport["resultats"]:
        print(f"{ligne['entree']!r} -> slug {ligne['slug_unique']!r}, fichier {ligne['nom_fichier_unique']!r}")
        if ligne["perdus"]:
            perdus = " ".join(f"{p['caractere']}({p['code']})" for p in ligne["perdus"])
            print(f"  perdus : {perdus}")
        for note in ligne["notes"]:
            print(f"  note : {note}")
    for genre in ("collisions_slug", "collisions_nom_fichier"):
        for groupe in rapport[genre]:
            print(f"collision ({genre[11:]}) {groupe['valeur']!r} : {groupe['entrees']}")
    print(f"{rapport['denominateur']} entrée(s) ; {len(rapport['entrees_avec_pertes'])} avec pertes, "
          f"{len(rapport['resultats_vides'])} résultat(s) vide(s), {len(rapport['collisions_slug'])} collision(s) "
          f"de slug, {len(rapport['collisions_nom_fichier'])} de nom de fichier")


def afficher_json(rapport: dict[str, Any]) -> None:
    print(json.dumps(rapport, ensure_ascii=False, indent=2))


def signaler_bibliotheques() -> None:
    """Une seule ligne sur stderr pour les bibliothèques facultatives absentes."""
    absentes = [nom for nom, module in (("unidecode", unidecode), ("python-slugify", python_slugify),
                                        ("pathvalidate", pathvalidate)) if module is None]
    if absentes:
        print(f"mode dégradé — absentes : {', '.join(absentes)} ; translittération stdlib seule, "
              "sans comparaison", file=sys.stderr)


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        description="Translittère du texte en ASCII et en tire un slug d'URL et un nom de fichier "
                    "sûr sur les trois systèmes, en signalant pertes et collisions.",
        epilog="Exemple : python translitterer_slug.py \"Crème brûlée à l'œuf\" --json\n"
               "          python translitterer_slug.py --fichier titres.txt --max-longueur 40\n"
               "Codes : 0 rien à signaler, 1 perte, slug vide ou collision, 2 entrée invalide, "
               "3 rien à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parseur.add_argument("textes", nargs="*", help="textes à traiter (un par argument)")
    parseur.add_argument("--fichier", type=Path, help="fichier UTF-8, une entrée par ligne")
    parseur.add_argument("--separateur", choices=SEPARATEURS, default="-", help="séparateur du slug")
    parseur.add_argument("--max-longueur", type=int, default=80, help="longueur maximale du slug")
    parseur.add_argument("--max-octets-nom", type=int, default=255, help="octets maximum d'un nom de fichier")
    parseur.add_argument("--garder-casse", action="store_true", help="slug sans mise en minuscules")
    parseur.add_argument("--remplacement", choices=("_", "-"), default="_",
                         help="caractère qui remplace les interdits dans un nom de fichier")
    parseur.add_argument("--max-octets", type=int, default=16 << 20, help="taille maximale de --fichier")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="dossier de base d'un --fichier relatif (défaut : dossier courant)")
    return parseur


def base_relative(racine: Path | None) -> Path:
    """Base des chemins relatifs : --racine, sinon le dossier courant (sinon celui de l'outil)."""
    if racine is not None:
        return racine
    try:
        return Path.cwd()
    except OSError:
        return RACINE


def preparer(args: argparse.Namespace) -> tuple[list[str], int, Reglages]:
    """Rassemble les entrées et valide les bornes ; lève EntreeInvalide."""
    if args.max_longueur < 8 or args.max_octets_nom < 16:
        raise EntreeInvalide("--max-longueur ≥ 8 et --max-octets-nom ≥ 16")
    entrees, ignorees = [nettoyer(t) for t in args.textes], 0
    if args.fichier is not None:
        chemin = args.fichier if args.fichier.is_absolute() else base_relative(args.racine) / args.fichier
        lues, ignorees = lire_entrees_fichier(chemin, args.max_octets)
        entrees += lues
    if not args.textes and args.fichier is None:
        raise EntreeInvalide("donner au moins un texte ou --fichier")
    reglages = Reglages(args.separateur, args.max_longueur, args.max_octets_nom,
                        not args.garder_casse, args.remplacement)
    return entrees, ignorees, reglages


def main() -> int:
    args = construire_parseur().parse_args()
    try:
        entrees, ignorees, reglages = preparer(args)
    except EntreeInvalide as exc:
        print(f"entrée invalide : {exc}", file=sys.stderr)
        return 2
    signaler_bibliotheques()
    rapport = construire_rapport(traiter(entrees, reglages), ignorees, reglages)
    if args.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    if not entrees:
        print("dénominateur nul : aucune entrée non vide, rien à examiner", file=sys.stderr)
        return 3
    defaut = (rapport["entrees_avec_pertes"] or rapport["resultats_vides"]
              or rapport["collisions_slug"] or rapport["collisions_nom_fichier"])
    return 1 if defaut else 0


if __name__ == "__main__":
    raise SystemExit(main())
