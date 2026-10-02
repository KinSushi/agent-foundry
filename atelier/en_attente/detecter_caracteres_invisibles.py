"""Repérer, ligne, colonne et point de code à l'appui, les caractères qu'un éditeur, une revue
de code ou un agent ne voient pas : invisibles, contrôles bidirectionnels, homoglyphes.

Mesuré dans cette session : un fichier de 3 lignes reprenant l'exemple « stretched string »
de CVE-2021-42574 (acces_niveau = "user", puis un test `!= "user..."` dont la chaîne porte 4
contrôles bidirectionnels) imprime « Vous etes administrateur. » sous python3.14 ;
sur les 196 fichiers .py présents dans outils/ lors de la mesure, cet outil a trouvé en 1,0 s
un U+202E littéral dans une chaîne (lire_email.py, ligne 558) et deux U+FEFF hors tête de
fichier (lire_srt_vtt.py, lignes 483 et 493), écrits tels quels au lieu de \\u202e, \\ufeff.

QUESTION
    Ce code ou ce texte cache-t-il des caractères invisibles, des inversions
    bidirectionnelles (Trojan Source, CVE-2021-42574) ou des homoglyphes dans les
    identifiants ?
MESURE
    Lecture de chaque fichier texte (UTF-8, repli cp1252 strict ; UTF-16/32 si BOM) et
    inspection caractère par caractère de tout ce qui n'est pas ASCII imprimable :
    contrôles bidirectionnels U+202A-U+202E et U+2066-U+2069 (avec équilibre ouvrant /
    fermant par ligne), marques LRM/RLM/ALM, largeur nulle et remplisseurs (U+200B-U+200D,
    U+2060-U+2064, U+FEFF hors tête, U+00AD, U+3164...), caractères de balise
    U+E0000-U+E007F (texte caché révélé), sélecteurs de variante (suites décodées en
    octets), espaces exotiques (U+00A0, U+2000-U+200A, U+202F, U+3000...), contrôles C0/C1,
    autres caractères de format (catégorie Cf). Les caractères contigus d'une même famille
    forment un seul constat. Pour Python, le module tokenize situe chaque constat (code,
    chaîne, commentaire, identifiant) et fournit les identifiants ; pour les autres
    langages, les mots du texte servent d'identifiants. Chaque identifiant non ASCII est
    confronté à : mélange d'écritures (première partie du nom Unicode du caractère,
    combinaisons japonaise, chinoise et coréenne admises), forme NFKC (celle que Python
    utilise réellement), squelette par une table de 346 sosies vers l'ASCII extraite de
    confusables.txt, collision de squelettes dans le fichier, imitation d'un mot-clé ou
    d'une fonction native. Avec confusable_homoglyphs installé, is_dangerous() est passé
    sur les mêmes identifiants et la comparaison est rendue.
HYPOTHÈSES
    Les fichiers sont du texte Unicode ; un caractère invisible ou bidirectionnel dans du
    code n'a pas de raison légitime d'y être écrit littéralement (une séquence
    d'échappement \\u202e se lit, elle) ; la gravité dépend du contexte : ce qui est banal
    dans de la prose française (U+202F avant « : ») est suspect dans du code.
LIMITES
    Ne voit pas les homoglyphes hors identifiants (chaînes, URL) ; ne connaît pas les
    écritures Unicode exactes (approximation par le nom du caractère) ; la table de sosies
    ne couvre que les correspondances caractère unique vers lettre ou chiffre ASCII ; les
    fichiers binaires (octet nul), non décodables, plus gros que --taille-max, les liens
    symboliques et les dossiers .git, node_modules, venv, __pycache__ sont écartés (et
    listés). L'analyse des identifiants hors Python ne sait pas distinguer chaînes et
    commentaires. Un fichier Python que tokenize refuse est analysé caractère par
    caractère sans contexte au-delà de l'erreur.
CONTRE-EXEMPLES
    Faux négatif constaté : `def \\u0441\\u043e\\u0435(): ...` (trois lettres cyrilliques
    dont le squelette est « coe ») n'est pas signalé : ni mélange d'écritures, ni homologue
    ASCII dans le fichier, ni mot natif imité ; rien ne le compare au reste du projet.
    Faux positif constaté : `a = 2` puis `\\u03b1 = 0.5` (alpha grec voulu, code de calcul)
    est classé « sosie » de gravité élevée, alors que l'auteur l'a écrit sciemment.
INVOCATION
    {outil} {fichier} --json
DOMAINE
    Revue de code avant fusion, contributions externes, sorties de modèles collées dans
    le dépôt, fichiers de configuration ; pour de la prose, seuls les invisibles et les
    contrôles bidirectionnels sont pertinents (seuil réglable).
"""

from __future__ import annotations

import argparse
import bisect
import builtins
import io
import json
import keyword
import os
import re
import sys
import tokenize
import unicodedata
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from types import MappingProxyType

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from confusable_homoglyphs import confusables as sosies_tiers
except ImportError:
    sosies_tiers = None

RACINE = Path(__file__).resolve().parent

__all__ = ["analyser_texte", "analyser_chemins", "ecriture", "squelette", "main"]

TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_INVOCATION = "INVOCATION"
TITRES_CONTRAT = ("QUESTION", "MESURE", TITRE_HYPOTHESES, "LIMITES", "CONTRE-EXEMPLES",
                  TITRE_INVOCATION, "DOMAINE")
NORME_CVE = "CVE-2021-42574"
FORME_NFKC = "NFKC"

CODE_RIEN = 0
CODE_TROUVE = 1
CODE_USAGE = 2
CODE_VIDE = 3

GRAVITES = ("info", "faible", "moyenne", "elevee", "critique")
TAILLE_SONDE = 8192
EXAMINES_MAX = 200
CONSTATS_MAX = 1000
LARGEUR_EXTRAIT = 30
DOSSIERS_SAUTES = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv",
                             "venv", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache"})
EXT_PYTHON = frozenset({".py", ".pyi", ".pyw"})
EXT_PROSE = frozenset({".md", ".markdown", ".txt", ".rst", ".adoc", ".tex", ".po", ".pot",
                       ".csv", ".tsv", ".srt", ".vtt"})
JETONS_CHAINE = frozenset({"STRING", "FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END",
                           "TSTRING_START", "TSTRING_MIDDLE", "TSTRING_END"})

BIDI_FORMATAGE = MappingProxyType({0x202A: "LRE", 0x202B: "RLE", 0x202C: "PDF", 0x202D: "LRO",
                                   0x202E: "RLO", 0x2066: "LRI", 0x2067: "RLI", 0x2068: "FSI",
                                   0x2069: "PDI"})
BIDI_OUVRANTS_PDF = frozenset({0x202A, 0x202B, 0x202D, 0x202E})
BIDI_OUVRANTS_PDI = frozenset({0x2066, 0x2067, 0x2068})
BIDI_MARQUES = frozenset({0x200E, 0x200F, 0x061C})
INVISIBLES = frozenset({0x200B, 0x200C, 0x200D, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064,
                        0x180E, 0x00AD, 0x034F, 0x115F, 0x1160, 0x3164, 0xFFA0, 0x17B4,
                        0x17B5, 0xFEFF, 0x206A, 0x206B, 0x206C, 0x206D, 0x206E, 0x206F,
                        0xFFF9, 0xFFFA, 0xFFFB, 0x1D159, *range(0x1D173, 0x1D17B),
                        *range(0x1BCA0, 0x1BCA4)})
ESPACES = frozenset({0x00A0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000, 0x0085,
                     0x2028, 0x2029})
JOINTURES = frozenset({0x200C, 0x200D})
ECRITURES_A_JOINTURE = frozenset({"ARABIC", "SYRIAC", "DEVANAGARI", "BENGALI", "GURMUKHI",
                                  "GUJARATI", "ORIYA", "TAMIL", "TELUGU", "KANNADA",
                                  "MALAYALAM", "SINHALA", "MONGOLIAN", "KHMER", "MYANMAR"})
ECRITURES_COMPAT = frozenset({"FULLWIDTH", "HALFWIDTH", "MATHEMATICAL", "MODIFIER",
                              "SUPERSCRIPT", "SUBSCRIPT", "CIRCLED", "PARENTHESIZED",
                              "SQUARED", "NEGATIVE", "DOUBLE-STRUCK", "BLACK-LETTER",
                              "SCRIPT", "MICRO", "OHM", "KELVIN", "ANGSTROM"})
COMBINAISONS_ADMISES = (frozenset({"LATIN", "HAN", "HIRAGANA", "KATAKANA"}),
                        frozenset({"LATIN", "HAN", "BOPOMOFO"}),
                        frozenset({"LATIN", "HAN", "HANGUL"}))
MOTIF_CONTROLE_ASCII = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f]")
MOTIF_MOT = re.compile(r"[^\W\d][\w\u200c\u200d\u3164\uffa0\u115f\u1160]*")

# Sosies : un caractère non ASCII suivi de la lettre ou du chiffre ASCII qu'il imite.
# Extrait de confusables.txt (Unicode, via les données de confusable_homoglyphs 3.3.1) :
# lettres seules, invariantes par NFKC, dont le sosie est un seul caractère ASCII.
SOSIES_ASCII = (
    "\u0131i\u0184b\u018dg\u0196l\u01a6R\u01a72\u01b73\u01bc5\u01bds\u01c0l\u021c3\u02228"
    "\u02238\u0251a\u0261g\u0263y\u0269i\u026ai\u026fw\u028bu\u028fy\u037fJ\u0391A\u0392B"
    "\u0395E\u0396Z\u0397H\u0399l\u039aK\u039cM\u039dN\u039fO\u03a1P\u03a4T\u03a5Y\u03a7X"
    "\u03b1a\u03b3y\u03b9i\u03bdv\u03bfo\u03c1p\u03c3o\u03c5u\u03dcF\u03e82\u03f3j\u03faM"
    "\u0405S\u0406l\u0408J\u0410A\u0412B\u0415E\u04173\u041aK\u041cM\u041dH\u041eO\u0420P"
    "\u0421C\u0422T\u0423Y\u0425X\u042cb\u0430a\u04316\u0433r\u0435e\u043eo\u0440p\u0441c"
    "\u0443y\u0445x\u0455s\u0456i\u0458j\u0461w\u0474V\u0475v\u04aeY\u04afy\u04bbh\u04bde"
    "\u04c0l\u04cfi\u04e03\u0501d\u050cG\u051bq\u051cW\u051dw\u054dU\u054fS\u0555O\u0561w"
    "\u0563q\u0566q\u0570h\u0578n\u057cn\u057du\u0581g\u0584f\u0585o\u0b20O\u0d20o\u101do"
    "\u10e7y\u10ffo\u1200U\u12d0O\u13a0D\u13a1R\u13a2T\u13a5i\u13a9Y\u13aaA\u13abJ\u13acE"
    "\u13b3W\u13b7M\u13bbH\u13bdY\u13c0G\u13c2h\u13c3Z\u13ce4\u13cfb\u13d2R\u13d4W\u13d5S"
    "\u13d9V\u13daS\u13deL\u13dfC\u13e2P\u13e6K\u13e7d\u13ee6\u13f3G\u13f4B\u142fV\u144cU"
    "\u146dP\u146fd\u1472b\u148dJ\u14aaL\u14bf2\u1541x\u157cH\u157dx\u1587R\u15afb\u15b4F"
    "\u15c5A\u15deD\u15eaD\u15f0M\u15f7B\u16b7X\u16c1l\u16d5K\u16d6M\u1d04c\u1d0fo\u1d11o"
    "\u1d1cu\u1d20v\u1d21w\u1d22z\u1d26r\u1d83g\u1d8cy\u1e9df\u1effy\u2c85r\u2c8eH\u2c92l"
    "\u2c94K\u2c98M\u2c9aN\u2c9eO\u2c9fo\u2ca2P\u2ca3p\u2ca4C\u2ca5c\u2ca6T\u2ca8Y\u2cacX"
    "\u2cca9\u2ccc3\u2cd0L\u2cd26\u2d38V\u2d39E\u2d4fl\u2d54O\u2d55Q\u2d5dX\ua4d0B\ua4d1P"
    "\ua4d2d\ua4d3D\ua4d4T\ua4d6G\ua4d7K\ua4d9J\ua4daC\ua4dcZ\ua4ddF\ua4dfM\ua4e0N\ua4e1L"
    "\ua4e2S\ua4e3R\ua4e6V\ua4e7H\ua4eaW\ua4ebX\ua4ecY\ua4eeA\ua4f0E\ua4f2l\ua4f3O\ua4f4U"
    "\ua6442\ua647i\ua6dfV\ua731s\ua75a2\ua76a3\ua76e9\ua798F\ua799f\ua79fu\ua7ab3\ua7b2J"
    "\ua7b3X\ua7b4B\uab32e\uab35f\uab3do\uab47r\uab48r\uab4eu\uab52u\uab5ay\uab75i\uab81r"
    "\uab83w\uab93z\uaba9v\uabaas\uabafc\U00010282B\U00010286E\U00010287F\U0001028al"
    "\U00010290X\U00010292O\U00010295P\U00010296S\U00010297T\U000102a0A\U000102a1B"
    "\U000102a2C\U000102a5F\U000102abO\U000102b0M\U000102b1T\U000102b2Y\U000102b4X"
    "\U000102cfH\U00010301B\U00010302C\U00010309l\U00010311M\U00010315T\U00010317X"
    "\U0001031a8\U00010404O\U00010415C\U0001041bL\U00010420S\U0001042co\U0001043dc"
    "\U00010448s\U000104b4R\U000104c2O\U000104ceU\U000104d27\U000104eao\U000104f6u"
    "\U00010513N\U00010516O\U00010518K\U0001051cC\U0001051dV\U00010525F\U00010526L"
    "\U00010527X\U00011706v\U0001170aw\U0001170ew\U0001170fw\U000118a0V\U000118a2F"
    "\U000118a3L\U000118a4Y\U000118a6E\U000118a9Z\U000118ac9\U000118aeE\U000118af4"
    "\U000118b2L\U000118b5O\U000118b8U\U000118bb5\U000118bcT\U000118c0v\U000118c1s"
    "\U000118c2F\U000118c3i\U000118c4z\U000118c67\U000118c8o\U000118ca3\U000118cc9"
    "\U000118d56\U000118d69\U000118d7o\U000118d8u\U000118dcy\U00016f08V\U00016f0aT"
    "\U00016f16L\U00016f28l\U00016f35R\U00016f3aS\U00016f3b3\U00016f40A\U00016f42U"
    "\U00016f43Y"
)
TABLE_SOSIES = MappingProxyType({ord(SOSIES_ASCII[i]): SOSIES_ASCII[i + 1]
                                 for i in range(0, len(SOSIES_ASCII), 2)})


@dataclass
class Constat:
    """Un caractère (ou une suite contiguë) suspect, ou un identifiant douteux."""

    fichier: str
    ligne: int
    colonne: int
    categorie: str
    gravite: str
    points_de_code: list[str]
    noms: list[str]
    longueur: int
    contexte: str
    extrait: str
    detail: str = ""
    texte_revele: str = ""
    source: str = "stdlib"


@dataclass(frozen=True)
class Fichier:
    """Ce qui est connu d'un fichier avant l'inspection des caractères."""

    nom: str
    nature: str
    texte: str


def point_de_code(o: int) -> str:
    """Écriture U+XXXX d'un point de code."""
    return f"U+{o:04X}"


def nom_caractere(o: int) -> str:
    """Nom Unicode, ou une description quand le caractère n'en a pas (contrôles)."""
    return unicodedata.name(chr(o), "") or f"<{unicodedata.category(chr(o))} sans nom>"


def categorie_de(o: int) -> str:
    """Famille d'un point de code, ou chaîne vide s'il est anodin."""
    if o in BIDI_FORMATAGE:
        return "bidi"
    if o in BIDI_MARQUES:
        return "marque_bidi"
    if 0xE0000 <= o <= 0xE007F:
        return "balise"
    if 0xFE00 <= o <= 0xFE0F or 0xE0100 <= o <= 0xE01EF:
        return "selecteur"
    if o in INVISIBLES:
        return "invisible"
    if o in ESPACES:
        return "espace"
    if (o < 0x20 and o not in (0x09, 0x0A, 0x0C, 0x0D)) or 0x7F <= o <= 0x9F:
        return "controle"
    if o > 0x7F and unicodedata.category(chr(o)) == "Cf":
        return "format"
    return ""


def rendre_visible(texte: str) -> str:
    """Remplace tout caractère suspect ou non imprimable par ⟦U+XXXX⟧."""
    morceaux = []
    for ch in texte:
        o = ord(ch)
        if categorie_de(o) or not ch.isprintable():
            morceaux.append(f"\u27e6{point_de_code(o)}\u27e7")
        else:
            morceaux.append(ch)
    return "".join(morceaux)


def ecriture(ch: str) -> str:
    """Écriture approchée d'une lettre (premier mot de son nom Unicode) ; COMMUN sinon."""
    if ch.isascii():
        return "LATIN" if ch.isalpha() else "COMMUN"
    if not unicodedata.category(ch).startswith("L"):
        return "COMMUN"
    nom = unicodedata.name(ch, "")
    tete = nom.split(" ", 1)[0] if nom else ""
    if tete in ECRITURES_COMPAT or not tete:
        forme = unicodedata.normalize(FORME_NFKC, ch)
        if forme and forme != ch:
            return ecriture(forme[0])
    return "HAN" if tete == "CJK" else (tete or "INCONNUE")


def ecritures_de(texte: str) -> frozenset[str]:
    """Ensemble des écritures de lettres présentes dans un texte."""
    return frozenset(e for e in map(ecriture, texte) if e != "COMMUN")


def est_melange(ecritures: frozenset[str]) -> bool:
    """Vrai si les écritures mélangées ne forment pas une combinaison usuelle (UTS 39)."""
    return len(ecritures) > 1 and not any(ecritures <= c for c in COMBINAISONS_ADMISES)


def squelette(texte: str) -> str:
    """Forme NFKC dont chaque sosie connu est remplacé par la lettre ASCII qu'il imite."""
    return unicodedata.normalize(FORME_NFKC, texte).translate(TABLE_SOSIES)


def _ligne_droite_a_gauche(ligne: str) -> bool:
    """Vrai si la ligne contient des lettres d'écriture de droite à gauche."""
    return any(unicodedata.bidirectional(ch) in ("R", "AL") for ch in ligne if not ch.isascii())


def _entre_lettres_a_jointure(ligne: str, debut: int, fin: int) -> bool:
    """ZWJ/ZWNJ entre deux lettres d'une écriture qui s'en sert (arabe, indiennes...)."""
    if debut == 0 or fin >= len(ligne):
        return False
    return (ecriture(ligne[debut - 1]) in ECRITURES_A_JOINTURE
            and ecriture(ligne[fin]) in ECRITURES_A_JOINTURE)


def _autour_d_emoji(ligne: str, debut: int, fin: int) -> bool:
    """Vrai si la suite est précédée d'un symbole (emoji, pictogramme, chiffre de touche)."""
    if debut == 0:
        return False
    avant = ligne[debut - 1]
    return unicodedata.category(avant) in ("So", "Sk", "Sm", "Nd", "Po") or ord(avant) in (
        0xFE0F, 0x20E3) or 0x1F3FB <= ord(avant) <= 0x1F3FF


def _graduer_invisible(points: list[int], ligne: str, debut: int, fin: int,
                       nature: str) -> str:
    """Gravité d'une suite de caractères à largeur nulle."""
    if all(p in JOINTURES for p in points):
        if _entre_lettres_a_jointure(ligne, debut, fin):
            return "info"
        if 0x200D in points and _autour_d_emoji(ligne, debut, fin):
            return "info"
    if nature == "prose":
        return "faible" if set(points) == {0x00AD} else "moyenne"
    return "elevee"


def _graduer_selecteur(points: list[int], ligne: str, debut: int) -> str:
    """Un sélecteur seul après un symbole ou un idéogramme est normal ; une suite, non."""
    if len(points) != 1 or debut == 0:
        return "elevee"
    avant = ligne[debut - 1]
    if 0xFE00 <= points[0] <= 0xFE0F and not avant.isalpha() and not avant.isspace():
        return "info"
    if 0xE0100 <= points[0] and ecriture(avant) == "HAN":
        return "info"
    return "moyenne"


def _graduer_espace(contexte: str, nature: str) -> str:
    """Une espace exotique n'a d'effet que hors prose et hors commentaire."""
    if nature == "prose" or contexte == "commentaire":
        return "info"
    if contexte in ("code", "identifiant"):
        return "elevee"
    return "faible"


def graduer(categorie: str, points: list[int], ligne: str, debut: int, fin: int,
            contexte: str, nature: str) -> str:
    """Gravité d'un constat selon sa famille, son contexte et la nature du fichier."""
    if categorie == "bidi":
        return "moyenne" if nature == "prose" and _ligne_droite_a_gauche(ligne) else "critique"
    if categorie == "marque_bidi":
        return "info" if nature == "prose" else "moyenne"
    if categorie == "balise":
        return "info" if debut > 0 and ord(ligne[debut - 1]) == 0x1F3F4 else "elevee"
    if categorie == "selecteur":
        return _graduer_selecteur(points, ligne, debut)
    if categorie == "invisible":
        return _graduer_invisible(points, ligne, debut, fin, nature)
    if categorie == "espace":
        return _graduer_espace(contexte, nature)
    if categorie == "controle":
        if nature == "prose" or (contexte == "chaine" and points == [0x1B]):
            return "moyenne"
        return "elevee"
    return "info" if nature == "prose" else "moyenne"


def reveler(categorie: str, points: list[int]) -> str:
    """Texte caché dans une suite de balises, de sélecteurs ou de largeurs nulles binaires."""
    if categorie == "balise":
        return "".join(chr(p - 0xE0000) for p in points if 0xE0020 <= p <= 0xE007E)
    if categorie == "selecteur" and len(points) > 1:
        octets = bytes(p - 0xFE00 if p <= 0xFE0F else p - 0xE0100 + 16 for p in points)
        return octets.decode("utf-8", errors="replace")
    if categorie == "invisible" and len(points) >= 8 and len(set(points)) == 2:
        zero, un = sorted(set(points))
        bits = "".join("1" if p == un else "0" for p in points)
        octets = bytes(int(bits[i:i + 8], 2) for i in range(0, len(bits) - 7, 8))
        texte = octets.decode("utf-8", errors="replace")
        return texte if texte.isprintable() else ""
    return ""


def _desequilibre_bidi(ligne: str) -> int:
    """Nombre d'ouvrants bidirectionnels non refermés en fin de ligne."""
    pile_pdf = pile_pdi = 0
    for ch in ligne:
        o = ord(ch)
        if o in BIDI_OUVRANTS_PDF:
            pile_pdf += 1
        elif o == 0x202C and pile_pdf:
            pile_pdf -= 1
        elif o in BIDI_OUVRANTS_PDI:
            pile_pdi += 1
        elif o == 0x2069 and pile_pdi:
            pile_pdi -= 1
    return pile_pdf + pile_pdi


def _suites_suspectes(ligne: str) -> Iterator[tuple[int, int, str]]:
    """(début, fin, famille) de chaque suite contiguë de caractères d'une même famille."""
    i = 0
    while i < len(ligne):
        cat = categorie_de(ord(ligne[i]))
        if not cat:
            i += 1
            continue
        j = i + 1
        while j < len(ligne) and categorie_de(ord(ligne[j])) == cat:
            j += 1
        yield i, j, cat
        i = j


@dataclass(frozen=True)
class Contexte:
    """Zones de chaînes et de commentaires d'un fichier Python, et ses identifiants."""

    debuts: tuple[tuple[int, int], ...]
    zones: tuple[tuple[tuple[int, int], tuple[int, int], str], ...]
    noms: tuple[tuple[str, int, int], ...]
    erreur: str


def contexte_python(texte: str) -> Contexte:
    """Découpe un source Python par tokenize ; s'arrête proprement sur une erreur."""
    zones: list[tuple[tuple[int, int], tuple[int, int], str]] = []
    noms: list[tuple[str, int, int]] = []
    erreur = ""
    try:
        for jeton in tokenize.generate_tokens(io.StringIO(texte).readline):
            genre = tokenize.tok_name[jeton.type]
            if genre == "COMMENT":
                zones.append((jeton.start, jeton.end, "commentaire"))
            elif genre in JETONS_CHAINE:
                zones.append((jeton.start, jeton.end, "chaine"))
            elif genre == "NAME":
                zones.append((jeton.start, jeton.end, "identifiant"))
                noms.append((jeton.string, jeton.start[0], jeton.start[1]))
    except (SyntaxError, tokenize.TokenError) as exc:
        erreur = f"tokenize : {exc}"
    zones.sort()
    return Contexte(tuple(z[0] for z in zones), tuple(zones), tuple(noms), erreur)


def situer(ctx: Contexte | None, ligne: int, colonne: int) -> str:
    """Contexte (code, chaine, commentaire, identifiant) d'une position, ou inconnu."""
    if ctx is None:
        return "inconnu"
    position = (ligne, colonne)
    rang = bisect.bisect_right(ctx.debuts, position) - 1
    if rang >= 0:
        debut, fin, genre = ctx.zones[rang]
        if debut <= position < fin:
            return genre
        if ctx.erreur and rang == len(ctx.zones) - 1:
            return "inconnu"
    return "code"


def _constat_suite(fichier: Fichier, numero: int, ligne: str, suite: tuple[int, int, str],
                   points: list[int], ctx: Contexte | None) -> Constat:
    """Construit le constat d'une suite de caractères suspects (points : ceux retenus)."""
    debut, fin, cat = suite
    contexte = situer(ctx, numero, debut)
    if cat == "invisible" and numero == 1 and debut == 0 and points == [0xFEFF]:
        cat, gravite, contexte = "bom", "info", "tete"
    else:
        gravite = graduer(cat, points, ligne, debut, fin, contexte, fichier.nature)
    uniques = list(dict.fromkeys(points))[:8]
    extrait = rendre_visible(ligne[max(0, debut - LARGEUR_EXTRAIT):fin + LARGEUR_EXTRAIT])
    detail = ""
    if cat == "bidi" and _desequilibre_bidi(ligne):
        detail = "ouvrant non refermé en fin de ligne (signature Trojan Source)"
    return Constat(fichier.nom, numero, debut + 1, cat, gravite,
                   [point_de_code(p) for p in uniques], [nom_caractere(p) for p in uniques],
                   len(points), contexte, extrait.strip(), detail, reveler(cat, points))


def analyser_caracteres(fichier: Fichier, ctx: Contexte | None) -> list[Constat]:
    """Constats ligne par ligne ; les contrôles bidirectionnels d'une ligne n'en font qu'un."""
    constats = []
    for numero, ligne in enumerate(fichier.texte.split("\n"), start=1):
        ligne = ligne.removesuffix("\r")
        if ligne.isascii() and not MOTIF_CONTROLE_ASCII.search(ligne):
            continue
        suites = list(_suites_suspectes(ligne))
        bidi = [s for s in suites if s[2] == "bidi"]
        if bidi:
            points = [ord(c) for d, f, _ in bidi for c in ligne[d:f]]
            constats.append(_constat_suite(fichier, numero, ligne,
                                           (bidi[0][0], bidi[-1][1], "bidi"), points, ctx))
        for suite in suites:
            if suite[2] != "bidi":
                points = [ord(c) for c in ligne[suite[0]:suite[1]]]
                constats.append(_constat_suite(fichier, numero, ligne, suite, points, ctx))
    return constats


def _identifiants(fichier: Fichier, ctx: Contexte | None) -> dict[str, tuple[int, int, int]]:
    """Identifiant tel qu'écrit -> (ligne, colonne de la 1re occurrence, occurrences)."""
    vus: dict[str, tuple[int, int, int]] = {}
    if ctx is not None:
        sources = ((nom, ligne, col) for nom, ligne, col in ctx.noms)
    else:
        sources = ((m.group(), numero, m.start())
                   for numero, ligne in enumerate(fichier.texte.split("\n"), start=1)
                   if not ligne.isascii() for m in MOTIF_MOT.finditer(ligne))
    for nom, ligne, col in sources:
        if not nom.isidentifier():
            continue
        premier = vus.get(nom)
        vus[nom] = (ligne, col, 1) if premier is None else (premier[0], premier[1],
                                                             premier[2] + 1)
    return vus


def _raisons_identifiant(nom: str, squelettes: dict[str, set[str]], ecrits: set[str],
                         nature: str) -> list[tuple[str, str, str]]:
    """(catégorie, gravité, explication) de chaque soupçon pesant sur un identifiant."""
    raisons = []
    forme = unicodedata.normalize(FORME_NFKC, nom)
    sq = squelette(forme)
    ecr = ecritures_de(forme)
    if est_melange(ecr):
        raisons.append(("melange_ecritures", "elevee", "écritures mêlées : " + ", ".join(
            sorted(ecr))))
    autres = sorted(squelettes.get(sq, set()) - {forme})
    if autres:
        raisons.append(("sosie", "elevee", "se confond avec : " + ", ".join(autres[:5])))
    if sq.isascii() and sq != forme and (keyword.iskeyword(sq) or hasattr(builtins, sq)):
        raisons.append(("imite_ascii", "elevee", f"se lit comme le nom Python « {sq} »"))
    if forme != nom and nature == "python":
        gravite = "elevee" if forme in ecrits else "moyenne"
        raisons.append(("normalise_nfkc", gravite, f"Python le lit comme « {forme} »"))
    return raisons


def analyser_identifiants(fichier: Fichier, ctx: Contexte | None) -> list[Constat]:
    """Constats sur les identifiants non ASCII : mélange, sosie, imitation, NFKC."""
    vus = _identifiants(fichier, ctx)
    squelettes: dict[str, set[str]] = {}
    for nom in vus:
        forme = unicodedata.normalize(FORME_NFKC, nom)
        squelettes.setdefault(squelette(forme), set()).add(forme)
    ecrits = set(vus)
    constats = []
    for nom, (ligne, col, nombre) in vus.items():
        if nom.isascii():
            continue
        raisons = _raisons_identifiant(nom, squelettes, ecrits, fichier.nature)
        if raisons:
            constats.append(_constat_identifiant(fichier, nom, ligne, col, nombre, raisons))
    return constats


def _constat_identifiant(fichier: Fichier, nom: str, ligne: int, col: int, nombre: int,
                         raisons: list[tuple[str, str, str]]) -> Constat:
    """Un constat par identifiant douteux, à sa gravité la plus haute."""
    pire = max(raisons, key=lambda r: GRAVITES.index(r[1]))
    non_ascii = list(dict.fromkeys(ord(c) for c in nom if not c.isascii()))[:8]
    return Constat(fichier.nom, ligne, col + 1, pire[0], pire[1],
                   [point_de_code(p) for p in non_ascii], [nom_caractere(p) for p in non_ascii],
                   nombre, "identifiant", rendre_visible(nom),
                   " ; ".join(r[2] for r in raisons) + f" (squelette : {squelette(nom)})")


def nature_de(chemin: Path) -> str:
    """python, prose ou code selon l'extension."""
    suffixe = chemin.suffix.lower()
    if suffixe in EXT_PYTHON:
        return "python"
    return "prose" if suffixe in EXT_PROSE else "code"


def analyser_texte(nom: str, texte: str, nature: str) -> tuple[list[Constat], str]:
    """Analyse complète d'un texte déjà décodé ; rend (constats, note éventuelle)."""
    fichier = Fichier(nom, nature, texte)
    ctx = contexte_python(texte) if nature == "python" else None
    constats = analyser_caracteres(fichier, ctx)
    if nature != "prose":
        constats.extend(analyser_identifiants(fichier, ctx))
    return constats, (ctx.erreur if ctx is not None else "")


def decoder(octets: bytes) -> tuple[str, str]:
    """(texte, encodage) ; texte vide et raison si binaire ou indécodable."""
    for bom, encodage in ((b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32"),
                          (b"\xff\xfe", "utf-16"), (b"\xfe\xff", "utf-16")):
        if octets.startswith(bom):
            try:
                return octets.decode(encodage), encodage
            except UnicodeDecodeError:
                return "", f"BOM {encodage} mais contenu indécodable"
    if b"\x00" in octets[:TAILLE_SONDE]:
        return "", "binaire (octet nul)"
    try:
        return octets.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    try:
        return octets.decode("cp1252"), "cp1252"
    except UnicodeDecodeError:
        return "", "ni UTF-8 ni cp1252"


def _fichiers_sous(dossier: Path, ignores: list[dict[str, str]],
                   affichage: Path) -> Iterator[Path]:
    """Parcourt un dossier sans suivre les liens ; note ce qui est sauté."""
    for courant, sous, fichiers in os.walk(dossier):
        base = Path(courant)
        for nom in sorted(sous):
            if nom in DOSSIERS_SAUTES or (base / nom).is_symlink():
                ignores.append({"chemin": _nom_affiche(base / nom, affichage),
                                "raison": "dossier sauté"})
        sous[:] = sorted(n for n in sous if n not in DOSSIERS_SAUTES
                         and not (base / n).is_symlink())
        for nom in sorted(fichiers):
            yield base / nom


def _cibles(chemins: list[Path], ignores: list[dict[str, str]],
            affichage: Path) -> Iterator[Path]:
    """Développe les arguments en fichiers à examiner."""
    for chemin in chemins:
        if chemin.is_dir():
            yield from _fichiers_sous(chemin, ignores, affichage)
        else:
            yield chemin


def _nom_affiche(chemin: Path, base: Path) -> str:
    """Chemin relatif à la base quand c'est possible."""
    try:
        return str(chemin.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(chemin)


def lire(chemin: Path, taille_max: int) -> tuple[str, str]:
    """(texte, raison d'écart) : lecture bornée et décodage."""
    if chemin.is_symlink():
        return "", "lien symbolique non suivi"
    try:
        if chemin.stat().st_size > taille_max:
            return "", f"plus de {taille_max} octets"
        with chemin.open("rb") as flux:
            octets = flux.read(taille_max + 1)
    except OSError as exc:
        return "", f"illisible ({exc.strerror or exc})"
    texte, encodage = decoder(octets)
    return (texte, "") if texte or encodage in ("utf-8", "cp1252") else ("", encodage)


def comparer_tiers(constats: list[Constat], identifiants: dict[str, str]) -> tuple[
        list[Constat], dict[str, object]]:
    """Passe confusable_homoglyphs.is_dangerous sur les identifiants non ASCII."""
    signales = {(c.fichier, c.extrait) for c in constats if c.contexte == "identifiant"}
    ajouts: list[Constat] = []
    seulement_stdlib, seulement_tiers, accords = [], [], 0
    for cle, fichier in sorted(identifiants.items()):
        nom = cle.split("\x00", 1)[1]
        tiers = bool(sosies_tiers.is_dangerous(nom))
        propre = (fichier, rendre_visible(nom)) in signales
        if tiers == propre:
            accords += tiers
        elif propre:
            seulement_stdlib.append(f"{fichier}: {rendre_visible(nom)}")
        else:
            seulement_tiers.append(f"{fichier}: {rendre_visible(nom)}")
            ajouts.append(Constat(fichier, 0, 0, "identifiant_dangereux", "elevee", [], [],
                                  1, "identifiant", rendre_visible(nom),
                                  "is_dangerous() : écritures mêlées et sosie", "",
                                  "confusable_homoglyphs"))
    return ajouts, {"identifiants_non_ascii": len(identifiants), "accords_signales": accords,
                    "seulement_stdlib": seulement_stdlib,
                    "seulement_confusable_homoglyphs": seulement_tiers}


def _non_ascii(nom: str, texte: str, nature: str) -> dict[str, str]:
    """Identifiants non ASCII d'un fichier, pour la comparaison avec la bibliothèque."""
    if nature == "prose":
        return {}
    ctx = contexte_python(texte) if nature == "python" else None
    vus = _identifiants(Fichier(nom, nature, texte), ctx)
    return {f"{nom}\x00{n}": nom for n in vus if not n.isascii()}


def analyser_chemins(chemins: list[Path], options: argparse.Namespace) -> dict[str, object]:
    """Analyse de tous les fichiers ; rend le rapport (sans le contrat)."""
    examines: list[str] = []
    ignores: list[dict[str, str]] = []
    notes: list[str] = []
    constats: list[Constat] = []
    non_ascii: dict[str, str] = {}
    tiers_actif = sosies_tiers is not None and not options.stdlib
    for chemin in _cibles(chemins, ignores, options.base):
        nom = _nom_affiche(chemin, options.base)
        texte, raison = lire(chemin, options.taille_max)
        if raison:
            ignores.append({"chemin": nom, "raison": raison})
            continue
        examines.append(nom)
        nature = nature_de(chemin)
        trouves, note = analyser_texte(nom, texte, nature)
        constats.extend(trouves)
        if note:
            notes.append(f"{nom}: {note}")
        if tiers_actif:
            non_ascii.update(_non_ascii(nom, texte, nature))
    comparaison: dict[str, object] = {}
    if tiers_actif:
        ajouts, comparaison = comparer_tiers(constats, non_ascii)
        constats.extend(ajouts)
    return _rapport(examines, ignores, notes, constats, options.seuil, comparaison,
                    tiers_actif)


def _rapport(examines: list[str], ignores: list[dict[str, str]], notes: list[str],
             constats: list[Constat], seuil: str, comparaison: dict[str, object],
             tiers_actif: bool) -> dict[str, object]:
    """Assemble le rapport, constats triés du plus grave au moins grave."""
    rang = GRAVITES.index(seuil)
    tries = sorted(constats, key=lambda c: (-GRAVITES.index(c.gravite), c.fichier, c.ligne,
                                            c.colonne))
    retenus = [c for c in tries if GRAVITES.index(c.gravite) >= rang]
    rapport: dict[str, object] = {
        "denominateur": len(examines),
        "examines": examines[:EXAMINES_MAX],
        "examines_tronques": len(examines) > EXAMINES_MAX,
        "moteur": "confusable_homoglyphs" if tiers_actif else "stdlib",
        "verdict": "CARACTÈRES SUSPECTS" if retenus else "RIEN AU-DESSUS DU SEUIL",
        "seuil": seuil,
        "nombre_constats": len(constats),
        "constats_au_dessus_du_seuil": len(retenus),
        "par_gravite": dict(Counter(c.gravite for c in constats)),
        "par_categorie": dict(Counter(c.categorie for c in constats)),
        "constats": [asdict(c) for c in tries[:CONSTATS_MAX]],
        "constats_tronques": len(tries) > CONSTATS_MAX,
        "ignores": ignores,
        "notes": notes,
    }
    if tiers_actif:
        rapport["comparaison"] = comparaison
    return rapport


def extraire_contrat(doc: str) -> dict[str, str]:
    """Sections du contrat de mesure lues dans la docstring du module."""
    sections: dict[str, list[str]] = {}
    courant = ""
    for ligne in doc.splitlines():
        if ligne.strip() in TITRES_CONTRAT and not ligne[:1].isspace():
            courant = ligne.strip()
            sections[courant] = []
        elif courant:
            sections[courant].append(ligne.strip())
    return {titre: " ".join(l for l in lignes if l) for titre, lignes in sections.items()}


def afficher_json(rapport: dict[str, object]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(rapport, ensure_ascii=False, indent=2))


def afficher_humain(rapport: dict[str, object]) -> None:
    """Résumé lisible : un constat par ligne, au-dessus du seuil."""
    print(f"{rapport['denominateur']} fichier(s) examiné(s) — moteur {rapport['moteur']} — "
          f"{rapport['verdict']} ({rapport['constats_au_dessus_du_seuil']} constat(s) "
          f"≥ {rapport['seuil']}, {rapport['nombre_constats']} au total)")
    rang = GRAVITES.index(str(rapport["seuil"]))
    for c in rapport["constats"]:
        if GRAVITES.index(c["gravite"]) < rang:
            continue
        revele = f" — texte caché : {c['texte_revele']!r}" if c["texte_revele"] else ""
        detail = f" — {c['detail']}" if c["detail"] else ""
        print(f"  {c['fichier']}:{c['ligne']}:{c['colonne']} [{c['gravite']}] {c['categorie']} "
              f"{','.join(c['points_de_code'])} ({c['contexte']}) {c['extrait']}{detail}{revele}")
    for i in rapport["ignores"][:20]:
        print(f"  ignoré : {i['chemin']} — {i['raison']}")


def _parseur() -> argparse.ArgumentParser:
    """Interface en ligne de commande."""
    p = argparse.ArgumentParser(
        description="Cherche les caractères invisibles, les contrôles bidirectionnels (Trojan "
                    "Source) et les homoglyphes d'identifiants dans des fichiers ou dossiers.",
        epilog=f"Exemple : python {RACINE.name}/detecter_caracteres_invisibles.py src/ --json "
               "(code 0 : rien au-dessus du seuil ; 1 : trouvé ; 2 : entrée invalide ; "
               "3 : rien à examiner)")
    p.add_argument("chemins", nargs="+", type=Path, help="fichiers ou dossiers à examiner")
    p.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    p.add_argument("--racine", type=Path, default=None,
                   help="base des chemins relatifs et des noms affichés (défaut : dossier courant)")
    p.add_argument("--seuil", choices=GRAVITES, default="moyenne",
                   help="gravité minimale qui fait rendre le code 1 (défaut : moyenne)")
    p.add_argument("--taille-max", type=float, default=20.0,
                   help="taille maximale d'un fichier lu, en Mo (défaut 20)")
    p.add_argument("--stdlib", action="store_true",
                   help="ignorer confusable_homoglyphs même s'il est installé")
    return p


def _valider(options: argparse.Namespace) -> str:
    """Message d'erreur d'usage, ou chaîne vide."""
    if not options.base.is_dir():
        return f"--racine n'est pas un dossier : {options.base}"
    if options.taille_max <= 0:
        return "--taille-max doit être positive"
    for chemin in options.resolus:
        if not chemin.exists():
            return f"chemin introuvable : {chemin}"
    return ""


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée : codes 0 (rien), 1 (trouvé), 2 (usage), 3 (rien à examiner)."""
    options = _parseur().parse_args(argv)
    options.base = options.racine if options.racine is not None else Path.cwd()
    options.resolus = [c if c.is_absolute() else options.base / c for c in options.chemins]
    erreur = _valider(options)
    if erreur:
        print(f"detecter_caracteres_invisibles : {erreur}", file=sys.stderr)
        return CODE_USAGE
    options.taille_max = int(options.taille_max * 1_000_000)
    if sosies_tiers is None and not options.stdlib:
        print("detecter_caracteres_invisibles : confusable_homoglyphs absent — mode dégradé "
              "stdlib (table de sosies intégrée), sans comparaison.", file=sys.stderr)
    rapport = analyser_chemins(options.resolus, options)
    rapport["contrat"] = extraire_contrat(__doc__ or "")
    if rapport["denominateur"] == 0:
        print("detecter_caracteres_invisibles : dénominateur nul — rien à examiner (aucun "
              f"fichier texte lisible ; {len(rapport['ignores'])} écarté(s)).", file=sys.stderr)
    if options.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    if rapport["denominateur"] == 0:
        return CODE_VIDE
    return CODE_TROUVE if rapport["constats_au_dessus_du_seuil"] else CODE_RIEN


if __name__ == "__main__":
    raise SystemExit(main())
