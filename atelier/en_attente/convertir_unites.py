"""Convertit une quantité physique en arithmétique rationnelle exacte et refuse les dimensions incompatibles.

Un agent qui convertit en flottants se trompe sans le voir : mesuré dans cette session,
`python3 -c "print(4.35*100)"` (4,35 m en cm) rend 434.99999999999994 et pint 0.26.1 rend
37.00000000000006 pour 98.6 °F en °C ; cet outil rend 435 et 37 exacts. Sur 30 conversions
confrontées à pint 0.26.1, 29 concordent (écart relatif ≤ 1,6e-15) ; la 30e, l'acre,
diffère de 4,0e-06 par définition (voir CONTRE-EXEMPLES).

QUESTION
    Combien vaut cette quantité dans cette unité, et les dimensions sont-elles
    compatibles ?
MESURE
    Analyse lexicale de l'expression d'unité (préfixes SI et binaires, produits,
    quotients, puissances, parenthèses), réduction de chaque unité à un facteur
    rationnel exact (fractions.Fraction) vers les unités SI cohérentes et à un
    vecteur de dimension à 8 composantes (7 dimensions SI + information, le bit) ;
    conversion affine pour K, °C, °F, °R. Facteurs tirés des définitions
    officielles : Brochure SI du BIPM (9e éd.), NIST SP 811 (annexe B), accord
    international de 1959 sur le yard et la livre. Si pint est installé, la même
    conversion est refaite par pint et l'écart relatif est publié.
HYPOTHÈSES
    L'unité est écrite en symboles (km/h, kg*m/s^2, m^3, MiB) ; « a/b*c » se lit
    (a/b)*c, comme en mathématiques ; « cal » est la calorie thermochimique
    (4,184 J) ; « Btu » est la Btu de la table internationale ; « acre » est l'acre
    international (43 560 pieds internationaux au carré) ; un MB vaut 10^6 octets.
LIMITES
    Deux grandeurs de même dimension restent convertibles même si la conversion n'a
    pas de sens physique (Gy et Sv, Hz et Bq, J et N*m) ; pas d'unité monétaire, ni
    logarithmique (dB), ni d'unité dépendant d'un état (calorie alimentaire
    régionale, mile terrestre US d'arpentage) ; « gal » et « ton » sont refusés
    comme ambigus ; exposants entiers seulement.
CONTRE-EXEMPLES
    « 10 °C » vers °F rend 50 °F (température absolue) alors qu'un écart de 10 °C
    vaut 18 °F : constaté dans cette session, l'option --ecart est nécessaire.
    « 1 Gy » vers Sv rend 1 sans broncher (même dimension m^2*s^-2), alors que la
    conversion exige un facteur de pondération biologique. « 1 acre » vers m^2 :
    pint 0.26.1 rend 4046.87261 (pied d'arpentage US) contre 4046.8564224 ici.
INVOCATION
    {outil} "3 km" --vers m --json
DOMAINE
    Grandeurs physiques et informatiques usuelles (longueur, masse, temps,
    température, énergie, puissance, pression, données, vitesse, surface, volume,
    force, unités SI dérivées) exprimées en symboles simples ou Unicode.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import pint
except ImportError:
    pint = None

RACINE = Path(__file__).resolve().parent

INTITULE_QUESTION = "QUESTION"
INTITULE_MESURE = "MESURE"
INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_LIMITES = "LIMITES"
INTITULE_CONTRE_EXEMPLES = "CONTRE-EXEMPLES"
INTITULE_INVOCATION = "INVOCATION"
INTITULE_DOMAINE = "DOMAINE"
INTITULES = (
    INTITULE_QUESTION, INTITULE_MESURE, INTITULE_HYPOTHESES, INTITULE_LIMITES,
    INTITULE_CONTRE_EXEMPLES, INTITULE_INVOCATION, INTITULE_DOMAINE,
)

CODE_OK = 0
CODE_DEFAUT = 1
CODE_USAGE = 2
CODE_RIEN = 3

SOURCE_BIPM = "BIPM"
SOURCE_NIST = "NIST"
SOURCE_CEI = "CEI 80000-13"
SOURCE_SI = "Brochure SI 9e éd. (" + SOURCE_BIPM + ")"
SOURCE_SP811 = SOURCE_NIST + " SP 811, annexe B"

TOLERANCE_PINT = 1e-9
CHIFFRES_DEFAUT = 15
EXPOSANT_MAX_NOMBRE = 400
EXPOSANT_MAX_UNITE = 24

DIFFERENCES_CONNUES = MappingProxyType({
    "acre": "pint définit l'acre d'arpentage US (survey foot 1200/3937 m) ; ici acre international (pied 0,3048 m)",
})

DIMENSIONS = ("m", "kg", "s", "A", "K", "mol", "cd", "bit")
LETTRES_DIMENSIONS = MappingProxyType({
    "m": "L", "kg": "M", "s": "T", "A": "I", "K": "Θ", "mol": "N", "cd": "J",
    "bit": "Info",
})

PREFIXES_SI = MappingProxyType({
    "Q": 30, "R": 27, "Y": 24, "Z": 21, "E": 18, "P": 15, "T": 12, "G": 9,
    "M": 6, "k": 3, "h": 2, "da": 1, "d": -1, "c": -2, "m": -3, "μ": -6,
    "u": -6, "n": -9, "p": -12, "f": -15, "a": -18, "z": -21, "y": -24,
    "r": -27, "q": -30,
})
PREFIXES_BINAIRES = MappingProxyType({
    "Ki": 10, "Mi": 20, "Gi": 30, "Ti": 40, "Pi": 50, "Ei": 60, "Zi": 70, "Yi": 80,
})

AMBIGUS = MappingProxyType({
    "gal": "ambiguë : gal_us (231 in^3 = 3,785411784 L) ou gal_imp (4,54609 L)",
    "ton": "ambiguë : t (tonne, 1000 kg) ; la « ton » courte ou longue n'est pas prise en charge",
    "KB": "ambiguë : kB (1000 octets) ou KiB (1024 octets)",
    "Kb": "ambiguë : kbit (1000 bits) ou Kibit (1024 bits)",
    "KiO": "mal écrite : écrire Kio (kibioctet)",
})

MOTIF_QUANTITE = re.compile(
    r"^\s*([+-]?(?:\d[\d_]*(?:[.,]\d*)?|[.,]\d+)(?:[eE][+-]?\d+)?)\s*(.*?)\s*$"
)
MOTIF_ATOME = re.compile(r"°?[^\W\d]+")
MOTIF_ENTIER = re.compile(r"-?\d+")


class ErreurUnite(Exception):
    """Erreur sur une entrée, portant le code de sortie à rendre."""

    def __init__(self, message: str, code: int = CODE_USAGE) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Unite:
    """Une unité nommée : facteur exact vers le SI cohérent, dimension, décalage affine."""

    symbole: str
    nom: str
    facteur: Fraction
    dimension: tuple[int, ...]
    prefixes: str = ""
    decalage: Fraction = Fraction(0)
    symbole_pint: str = ""
    source: str = SOURCE_SI


@dataclass(frozen=True)
class Composant:
    """Un atome d'une expression : unité, préfixe éventuel et puissance."""

    texte: str
    unite: Unite
    prefixe: str
    facteur_prefixe: Fraction
    puissance: int


@dataclass(frozen=True)
class Expression:
    """Une expression d'unité réduite : facteur, dimension et composants."""

    texte: str
    facteur: Fraction
    dimension: tuple[int, ...]
    composants: tuple[Composant, ...]


def dimension_de(**exposants: int) -> tuple[int, ...]:
    """Construit un vecteur de dimension à partir d'exposants nommés."""
    return tuple(exposants.get(nom, 0) for nom in DIMENSIONS)


def _unites_si() -> list[Unite]:
    """Unités de base et dérivées cohérentes du SI (Brochure SI, tableaux 2 et 4)."""
    d = dimension_de
    un = Fraction(1)
    return [
        Unite("m", "mètre", un, d(m=1), "si"),
        Unite("g", "gramme", Fraction(1, 1000), d(kg=1), "si"),
        Unite("s", "seconde", un, d(s=1), "si"),
        Unite("A", "ampère", un, d(A=1), "si"),
        Unite("K", "kelvin", un, d(K=1), "si"),
        Unite("mol", "mole", un, d(mol=1), "si"),
        Unite("cd", "candela", un, d(cd=1), "si"),
        Unite("rad", "radian", un, d(), "si", symbole_pint="radian"),
        Unite("sr", "stéradian", un, d(), "si", symbole_pint="steradian"),
        Unite("Hz", "hertz", un, d(s=-1), "si"),
        Unite("N", "newton", un, d(kg=1, m=1, s=-2), "si"),
        Unite("Pa", "pascal", un, d(kg=1, m=-1, s=-2), "si"),
        Unite("J", "joule", un, d(kg=1, m=2, s=-2), "si"),
        Unite("W", "watt", un, d(kg=1, m=2, s=-3), "si"),
        Unite("C", "coulomb", un, d(A=1, s=1), "si"),
        Unite("V", "volt", un, d(kg=1, m=2, s=-3, A=-1), "si"),
        Unite("F", "farad", un, d(kg=-1, m=-2, s=4, A=2), "si"),
        Unite("Ω", "ohm", un, d(kg=1, m=2, s=-3, A=-2), "si", symbole_pint="ohm"),
        Unite("ohm", "ohm", un, d(kg=1, m=2, s=-3, A=-2), "si"),
        Unite("S", "siemens", un, d(kg=-1, m=-2, s=3, A=2), "si"),
        Unite("Wb", "weber", un, d(kg=1, m=2, s=-2, A=-1), "si"),
        Unite("T", "tesla", un, d(kg=1, s=-2, A=-1), "si"),
        Unite("H", "henry", un, d(kg=1, m=2, s=-2, A=-2), "si"),
        Unite("lm", "lumen", un, d(cd=1), "si"),
        Unite("lx", "lux", un, d(cd=1, m=-2), "si"),
        Unite("Bq", "becquerel", un, d(s=-1), "si"),
        Unite("Gy", "gray", un, d(m=2, s=-2), "si"),
        Unite("Sv", "sievert", un, d(m=2, s=-2), "si"),
        Unite("kat", "katal", un, d(mol=1, s=-1), "si"),
    ]


def _unites_admises() -> list[Unite]:
    """Unités hors SI admises avec le SI (Brochure SI, tableau 8) et températures."""
    d = dimension_de
    return [
        Unite("min", "minute", Fraction(60), d(s=1)),
        Unite("h", "heure", Fraction(3600), d(s=1)),
        Unite("d", "jour", Fraction(86400), d(s=1)),
        Unite("L", "litre", Fraction(1, 1000), d(m=3), "si"),
        Unite("l", "litre", Fraction(1, 1000), d(m=3), "si", symbole_pint="L"),
        Unite("t", "tonne", Fraction(1000), d(kg=1), "si"),
        Unite("ha", "hectare", Fraction(10000), d(m=2)),
        Unite("au", "unité astronomique", Fraction(149597870700), d(m=1)),
        Unite("Å", "ångström", Fraction(1, 10**10), d(m=1), symbole_pint="angstrom"),
        Unite("eV", "électronvolt", Fraction("1.602176634e-19"), d(kg=1, m=2, s=-2), "si"),
        Unite("Wh", "wattheure", Fraction(3600), d(kg=1, m=2, s=-2), "si"),
        Unite("bar", "bar", Fraction(100000), d(kg=1, m=-1, s=-2), "si"),
        Unite("°C", "degré Celsius", Fraction(1), d(K=1), decalage=Fraction("273.15"), symbole_pint="degC"),
        Unite("degC", "degré Celsius", Fraction(1), d(K=1), decalage=Fraction("273.15")),
        Unite("°F", "degré Fahrenheit", Fraction(5, 9), d(K=1), decalage=Fraction("459.67") * Fraction(5, 9), symbole_pint="degF", source=SOURCE_SP811),
        Unite("degF", "degré Fahrenheit", Fraction(5, 9), d(K=1), decalage=Fraction("459.67") * Fraction(5, 9), source=SOURCE_SP811),
        Unite("°R", "degré Rankine", Fraction(5, 9), d(K=1), symbole_pint="degR", source=SOURCE_SP811),
        Unite("degR", "degré Rankine", Fraction(5, 9), d(K=1), source=SOURCE_SP811),
    ]


def _unites_donnees() -> list[Unite]:
    """Unités d'information (CEI 80000-13) : bit, octet."""
    d = dimension_de
    return [
        Unite("bit", "bit", Fraction(1), d(bit=1), "si+bin", source=SOURCE_CEI),
        Unite("B", "octet", Fraction(8), d(bit=1), "si+bin", source=SOURCE_CEI),
        Unite("o", "octet", Fraction(8), d(bit=1), "si+bin", symbole_pint="B", source=SOURCE_CEI),
    ]


def _unites_anglo_saxonnes() -> list[Unite]:
    """Unités usuelles hors SI, valeurs exactes du NIST SP 811 (annexe B)."""
    d = dimension_de
    pied, pouce, livre = Fraction("0.3048"), Fraction("0.0254"), Fraction("0.45359237")
    g_n = Fraction("9.80665")
    lbf = livre * g_n
    cal_it = Fraction("4.1868")
    return [
        Unite("in", "pouce", pouce, d(m=1), source=SOURCE_SP811),
        Unite("ft", "pied", pied, d(m=1), source=SOURCE_SP811),
        Unite("yd", "yard", Fraction("0.9144"), d(m=1), source=SOURCE_SP811),
        Unite("mi", "mile", Fraction("1609.344"), d(m=1), source=SOURCE_SP811),
        Unite("nmi", "mille marin", Fraction(1852), d(m=1), source=SOURCE_SP811),
        Unite("lb", "livre", livre, d(kg=1), source=SOURCE_SP811),
        Unite("oz", "once avoirdupois", livre / 16, d(kg=1), source=SOURCE_SP811),
        Unite("lbf", "livre-force", lbf, d(kg=1, m=1, s=-2), source=SOURCE_SP811),
        Unite("kgf", "kilogramme-force", g_n, d(kg=1, m=1, s=-2), source=SOURCE_SP811),
        Unite("dyn", "dyne", Fraction(1, 10**5), d(kg=1, m=1, s=-2), source=SOURCE_SP811),
        Unite("erg", "erg", Fraction(1, 10**7), d(kg=1, m=2, s=-2), source=SOURCE_SP811),
        Unite("psi", "livre-force par pouce carré", lbf / pouce**2, d(kg=1, m=-1, s=-2), source=SOURCE_SP811),
        Unite("atm", "atmosphère normale", Fraction(101325), d(kg=1, m=-1, s=-2), source=SOURCE_SP811),
        Unite("Torr", "torr", Fraction(101325, 760), d(kg=1, m=-1, s=-2), symbole_pint="torr", source=SOURCE_SP811),
        Unite("mmHg", "millimètre de mercure conventionnel", Fraction("13595.1") * g_n / 1000, d(kg=1, m=-1, s=-2), source=SOURCE_SP811),
        Unite("cal", "calorie thermochimique", Fraction("4.184"), d(kg=1, m=2, s=-2), "si", source=SOURCE_SP811),
        Unite("cal_IT", "calorie de la table internationale", cal_it, d(kg=1, m=2, s=-2), "si", symbole_pint="cal_it", source=SOURCE_SP811),
        Unite("Btu", "Btu (table internationale)", cal_it * 1000 * livre * Fraction(5, 9), d(kg=1, m=2, s=-2), symbole_pint="Btu_it", source=SOURCE_SP811),
        Unite("hp", "cheval-vapeur mécanique (550 ft·lbf/s)", 550 * pied * lbf, d(kg=1, m=2, s=-3), source=SOURCE_SP811),
        Unite("ch", "cheval-vapeur métrique (75 kgf·m/s)", 75 * g_n, d(kg=1, m=2, s=-3), symbole_pint="metric_horsepower", source=SOURCE_SP811),
        Unite("mph", "mile par heure", Fraction("1609.344") / 3600, d(m=1, s=-1), source=SOURCE_SP811),
        Unite("kn", "nœud", Fraction(1852, 3600), d(m=1, s=-1), symbole_pint="knot", source=SOURCE_SP811),
        Unite("gal_us", "gallon US", 231 * pouce**3, d(m=3), symbole_pint="gallon", source=SOURCE_SP811),
        Unite("gal_imp", "gallon impérial", Fraction("0.00454609"), d(m=3), symbole_pint="imperial_gallon", source=SOURCE_SP811),
        Unite("acre", "acre international", 43560 * pied**2, d(m=2), source=SOURCE_SP811),
    ]


def construire_table() -> Mapping[str, Unite]:
    """Assemble la table immuable des unités, indexée par symbole."""
    toutes = _unites_si() + _unites_admises() + _unites_donnees() + _unites_anglo_saxonnes()
    return MappingProxyType({u.symbole: u for u in toutes})


UNITES = construire_table()


def normaliser_texte_unite(texte: str) -> str:
    """Normalise l'Unicode (NFKC) : exposants, micro, ohm, signes moins."""
    texte = unicodedata.normalize("NFKC", texte)
    for ancien, nouveau in (("−", "-"), ("**", "^"), ("·", "*"), ("×", "*"), ("⋅", "*")):
        texte = texte.replace(ancien, nouveau)
    return texte.strip()


def chercher_atome(atome: str) -> tuple[Unite, str, Fraction]:
    """Trouve l'unité d'un atome, avec préfixe SI ou binaire éventuel."""
    if atome in UNITES:
        return UNITES[atome], "", Fraction(1)
    candidats = sorted(list(PREFIXES_BINAIRES) + list(PREFIXES_SI), key=len, reverse=True)
    for prefixe in candidats:
        reste = atome[len(prefixe):]
        if not atome.startswith(prefixe) or reste not in UNITES:
            continue
        unite = UNITES[reste]
        if prefixe in PREFIXES_BINAIRES and unite.prefixes == "si+bin":
            return unite, prefixe, Fraction(2) ** PREFIXES_BINAIRES[prefixe]
        if prefixe in PREFIXES_SI and unite.prefixes in ("si", "si+bin"):
            return unite, prefixe, Fraction(10) ** PREFIXES_SI[prefixe]
    if atome in AMBIGUS:
        raise ErreurUnite(f"unité « {atome} » {AMBIGUS[atome]}")
    raise ErreurUnite(f"unité inconnue : « {atome} »")


def decouper_unite(texte: str) -> list[str]:
    """Découpe une expression d'unité en jetons : atomes, entiers, opérateurs."""
    jetons: list[str] = []
    i = 0
    while i < len(texte):
        car = texte[i]
        if car.isspace():
            i += 1
            continue
        if car in "()*/^.":
            jetons.append(car)
            i += 1
            continue
        trouve = MOTIF_ATOME.match(texte, i) or MOTIF_ENTIER.match(texte, i)
        if trouve is None:
            raise ErreurUnite(f"caractère inattendu « {car} » dans l'unité « {texte} »")
        jetons.append(trouve.group(0))
        i = trouve.end()
    return jetons


class _Analyseur:
    """Analyseur descendant récursif d'une expression d'unité."""

    def __init__(self, jetons: Sequence[str], texte: str) -> None:
        self.jetons = list(jetons)
        self.position = 0
        self.texte = texte
        self.avertissements: list[str] = []

    def courant(self) -> str | None:
        return self.jetons[self.position] if self.position < len(self.jetons) else None

    def consommer(self) -> str:
        jeton = self.jetons[self.position]
        self.position += 1
        return jeton

    def produit(self) -> list[tuple[str, int]]:
        termes = self.puissance()
        vu_division = False
        while self.courant() not in (None, ")"):
            operateur = self.courant()
            if operateur in ("*", "."):
                self.consommer()
                if vu_division:
                    self.avertissements.append(
                        f"« {self.texte} » : a/b*c est lu (a/b)*c ; mettez des parenthèses si vous vouliez a/(b*c)")
            if operateur == "/":
                self.consommer()
                vu_division = True
                termes += [(atome, -p) for atome, p in self.puissance()]
                continue
            termes += self.puissance()
        return termes

    def puissance(self) -> list[tuple[str, int]]:
        termes = self.primaire()
        if self.courant() == "^":
            self.consommer()
            exposant = self.entier_exposant()
            termes = [(atome, p * exposant) for atome, p in termes]
        return termes

    def entier_exposant(self) -> int:
        jeton = self.courant()
        if jeton == "(":
            self.consommer()
            valeur = self.entier_exposant()
            if self.courant() != ")":
                raise ErreurUnite(f"parenthèse fermante attendue dans « {self.texte} »")
            self.consommer()
            return valeur
        if jeton is None or not MOTIF_ENTIER.fullmatch(jeton):
            raise ErreurUnite(f"exposant entier attendu dans « {self.texte} »")
        return borner_exposant(int(self.consommer()), self.texte)

    def primaire(self) -> list[tuple[str, int]]:
        jeton = self.courant()
        if jeton is None:
            raise ErreurUnite(f"expression d'unité incomplète : « {self.texte} »")
        if jeton == "(":
            self.consommer()
            termes = self.produit()
            if self.courant() != ")":
                raise ErreurUnite(f"parenthèse fermante manquante dans « {self.texte} »")
            self.consommer()
            return termes
        if jeton == "1":
            self.consommer()
            return []
        if not MOTIF_ATOME.fullmatch(jeton):
            raise ErreurUnite(f"jeton inattendu « {jeton} » dans « {self.texte} »")
        self.consommer()
        exposant = 1
        suivant = self.courant()
        if suivant is not None and MOTIF_ENTIER.fullmatch(suivant):
            exposant = borner_exposant(int(self.consommer()), self.texte)
        return [(jeton, exposant)]


def borner_exposant(exposant: int, texte: str) -> int:
    """Refuse les exposants d'unité démesurés (calcul exact coûteux, sens physique nul)."""
    if abs(exposant) > EXPOSANT_MAX_UNITE:
        raise ErreurUnite(f"exposant {exposant} hors bornes dans « {texte} » (|exposant| ≤ {EXPOSANT_MAX_UNITE})")
    return exposant


def analyser_unite(texte_brut: str) -> tuple[Expression, list[str]]:
    """Réduit une expression d'unité en facteur exact, dimension et composants."""
    texte = normaliser_texte_unite(texte_brut)
    if not texte:
        raise ErreurUnite("unité vide")
    analyseur = _Analyseur(decouper_unite(texte), texte)
    termes = analyseur.produit()
    if analyseur.courant() is not None:
        raise ErreurUnite(f"jeton en trop « {analyseur.courant()} » dans « {texte} »")
    facteur = Fraction(1)
    dimension = [0] * len(DIMENSIONS)
    composants = []
    for atome, puissance in termes:
        unite, prefixe, facteur_prefixe = chercher_atome(atome)
        facteur *= (unite.facteur * facteur_prefixe) ** puissance
        dimension = [a + b * puissance for a, b in zip(dimension, unite.dimension)]
        composants.append(Composant(atome, unite, prefixe, facteur_prefixe, puissance))
    return Expression(texte, facteur, tuple(dimension), tuple(composants)), analyseur.avertissements


def lire_nombre(texte: str) -> Fraction:
    """Convertit un nombre décimal (point ou virgule) en fraction exacte, exposant borné."""
    if "," in texte and "." in texte:
        raise ErreurUnite(f"nombre ambigu « {texte} » : point et virgule à la fois")
    if re.fullmatch(r"[+-]?\d{1,3},\d{3}", texte):
        raise ErreurUnite(f"nombre ambigu « {texte} » : virgule décimale ou séparateur de milliers ? "
                          f"écrivez {texte.replace(',', '.')} ou {texte.replace(',', '')}")
    exposant = re.search(r"[eE]([+-]?\d+)$", texte)
    if exposant and abs(int(exposant.group(1))) > EXPOSANT_MAX_NOMBRE:
        raise ErreurUnite(f"exposant hors bornes dans « {texte} » (|exposant| ≤ {EXPOSANT_MAX_NOMBRE})")
    return Fraction(texte.replace(",", ".").replace("_", ""))


def separer_quantite(texte: str) -> tuple[Fraction, str]:
    """Sépare « 3,5 km/h » en valeur exacte et texte d'unité."""
    trouve = MOTIF_QUANTITE.match(normaliser_texte_unite(texte))
    if trouve is None:
        raise ErreurUnite(f"quantité illisible : « {texte} » (attendu : nombre puis unité, ex. « 3 km »)")
    return lire_nombre(trouve.group(1)), trouve.group(2)


def est_affine(expression: Expression) -> bool:
    """Vrai si l'expression est une seule unité de température à décalage, puissance 1, sans préfixe."""
    if len(expression.composants) != 1:
        return False
    seul = expression.composants[0]
    return seul.unite.decalage != 0 and seul.puissance == 1 and not seul.prefixe


def ecrire_dimension(dimension: Sequence[int], lettres: bool) -> str:
    """Écrit une dimension en lettres (L·T^-1) ou en unités SI de base (m s^-1)."""
    morceaux = []
    for nom, exposant in zip(DIMENSIONS, dimension):
        if exposant == 0:
            continue
        symbole = LETTRES_DIMENSIONS[nom] if lettres else nom
        morceaux.append(symbole if exposant == 1 else f"{symbole}^{exposant}")
    if not morceaux:
        return "1"
    return ("·" if lettres else " ").join(morceaux)


def unites_nommees_de(dimension: tuple[int, ...]) -> list[str]:
    """Liste les unités SI dérivées nommées qui ont exactement cette dimension."""
    return [u.symbole for u in _unites_si()
            if u.dimension == dimension and u.facteur == 1 and any(u.dimension)
            and u.symbole not in DIMENSIONS and u.symbole not in ("ohm", "rad", "sr")]


def chiffres_exacts(valeur: Fraction) -> int | None:
    """Nombre de chiffres significatifs d'un décimal exact, None si le développement est infini."""
    reste, puissances = valeur.denominator, []
    for premier in (2, 5):
        compte = 0
        while reste % premier == 0:
            reste //= premier
            compte += 1
        puissances.append(compte)
    if reste != 1:
        return None
    return len(str(abs(valeur.numerator))) + max(puissances) + 2


def formater_fraction(valeur: Fraction, chiffres: int) -> tuple[str, bool]:
    """Écrit une fraction en décimal ; exact si le dénominateur n'a que les facteurs 2 et 5."""
    precision_exacte = chiffres_exacts(valeur)
    with localcontext() as contexte:
        contexte.prec = precision_exacte or chiffres
        decimal = Decimal(valeur.numerator) / Decimal(valeur.denominator)
    if decimal == 0:
        return "0", True
    if Decimal("1e-6") <= abs(decimal) < Decimal("1e21"):
        texte = format(decimal, "f")
        if "." in texte:
            texte = texte.rstrip("0").rstrip(".")
    else:
        texte = format(decimal.normalize(), "E")
    return texte, precision_exacte is not None


def en_flottant(valeur: Fraction) -> float | None:
    """Convertit en flottant, None si la valeur dépasse la plage des flottants."""
    try:
        return float(valeur)
    except OverflowError:
        return None


def notes_composants(expression: Expression) -> list[str]:
    """Signale les lectures conventionnelles qui pourraient surprendre."""
    notes = []
    for composant in expression.composants:
        if composant.unite.symbole in ("B", "o", "bit") and composant.prefixe in PREFIXES_SI:
            notes.append(f"{composant.texte} : préfixe SI décimal (10^{PREFIXES_SI[composant.prefixe]}) ; "
                         f"pour une puissance de 2, utilisez le préfixe binaire (Ki, Mi, Gi…)")
        if composant.unite.symbole == "cal":
            notes.append("cal = calorie thermochimique (4,184 J) ; cal_IT = 4,1868 J")
        if composant.unite.decalage != 0 and not est_affine(expression):
            notes.append(f"{composant.texte} dans une unité composée : lu comme un écart de température (sans décalage)")
    return notes


def vers_kelvin_ou_si(valeur: Fraction, expression: Expression, ecart: bool) -> Fraction:
    """Ramène la valeur en unités SI cohérentes (kelvin absolu si température affine)."""
    if est_affine(expression) and not ecart:
        return valeur * expression.facteur + expression.composants[0].unite.decalage
    return valeur * expression.facteur


def depuis_si(valeur_si: Fraction, expression: Expression, ecart: bool) -> Fraction:
    """Exprime une valeur SI dans l'unité cible (décalage affine si besoin)."""
    if est_affine(expression) and not ecart:
        return (valeur_si - expression.composants[0].unite.decalage) / expression.facteur
    return valeur_si / expression.facteur


def indice_incompatibilite(source: Expression, cible: Expression) -> str:
    """Propose une piste quand les dimensions diffèrent."""
    if all(a == -b for a, b in zip(source.dimension, cible.dimension)):
        return " (dimensions inverses l'une de l'autre)"
    symboles = {c.unite.symbole for c in source.composants + cible.composants}
    if symboles & {"C", "F"} and dimension_de(K=1) in (source.dimension, cible.dimension):
        return " (C est le coulomb et F le farad : pour les degrés, écrivez °C ou °F)"
    return ""


def symbole_pint(composant: Composant) -> str:
    """Symbole pint d'un composant, préfixe compris (μ s'écrit u chez pint)."""
    base = composant.unite.symbole_pint or composant.unite.symbole
    prefixe = "u" if composant.prefixe == "μ" else composant.prefixe
    return prefixe + base


def texte_pint(expression: Expression, ecart: bool) -> str:
    """Réécrit l'expression dans la syntaxe de pint, composant par composant."""
    if est_affine(expression):
        return ("delta_" if ecart else "") + symbole_pint(expression.composants[0])
    morceaux = [f"({symbole_pint(c)})**{c.puissance}" for c in expression.composants]
    return " * ".join(morceaux) if morceaux else "dimensionless"


def statut_pint(ecart_relatif: float, source: Expression, cible: Expression) -> tuple[str, str]:
    """Qualifie l'écart : accord, différence de définition connue, ou désaccord."""
    if ecart_relatif <= TOLERANCE_PINT:
        return "accord", ""
    for composant in source.composants + cible.composants:
        if composant.unite.symbole in DIFFERENCES_CONNUES:
            return "definition_differente", DIFFERENCES_CONNUES[composant.unite.symbole]
    return "desaccord", "écart inexpliqué : ne pas se fier au résultat sans vérifier la définition"


def controler_avec_pint(registre: Any, valeur: Fraction, source: Expression,
                        cible: Expression, ecart: bool, attendu: Fraction) -> dict[str, Any]:
    """Refait la conversion avec pint et publie l'écart relatif."""
    texte_source, texte_cible = texte_pint(source, ecart), texte_pint(cible, ecart)
    try:
        quantite = registre.Quantity(float(valeur), texte_source)
        obtenu = float(quantite.to(texte_cible).magnitude)
        reference = float(attendu)
    except (pint.errors.PintError, ValueError, TypeError, AttributeError, KeyError,
            ZeroDivisionError, OverflowError) as exc:
        return {"statut": "non comparable", "raison": f"{type(exc).__name__}: {exc}"}
    ecart_relatif = abs(obtenu - reference) / abs(reference) if reference else abs(obtenu)
    statut, explication = statut_pint(ecart_relatif, source, cible)
    return {"statut": statut, "valeur_pint": obtenu, "ecart_relatif": ecart_relatif,
            "expression_pint": f"{texte_source} -> {texte_cible}", "explication": explication}


def expression_si(dimension: tuple[int, ...]) -> Expression:
    """Construit l'expression SI cohérente (unités de base) d'une dimension."""
    texte = ecrire_dimension(dimension, lettres=False)
    if texte == "1":
        return Expression("1", Fraction(1), dimension, ())
    return analyser_unite(texte)[0]


def convertir(entree: str, vers: str | None, ecart: bool, chiffres: int,
              registre: Any) -> dict[str, Any]:
    """Convertit une quantité ; lève ErreurUnite sur entrée invalide ou dimension incompatible."""
    valeur, texte_unite = separer_quantite(entree)
    if not texte_unite:
        raise ErreurUnite(f"« {entree} » n'a pas d'unité")
    source, avertissements = analyser_unite(texte_unite)
    if vers:
        cible, avertissements_cible = analyser_unite(vers)
        avertissements += avertissements_cible
    else:
        cible = expression_si(source.dimension)
    if source.dimension != cible.dimension:
        raise ErreurUnite(
            f"dimensions incompatibles : {source.texte} [{ecrire_dimension(source.dimension, True)}] "
            f"et {cible.texte} [{ecrire_dimension(cible.dimension, True)}]"
            + indice_incompatibilite(source, cible), CODE_DEFAUT)
    resultat = depuis_si(vers_kelvin_ou_si(valeur, source, ecart), cible, ecart)
    return assembler_conversion(entree, valeur, source, cible, resultat, chiffres,
                                avertissements + notes_composants(source) + notes_composants(cible),
                                ecart, registre)


def assembler_conversion(entree: str, valeur: Fraction, source: Expression, cible: Expression,
                         resultat: Fraction, chiffres: int, notes: list[str], ecart: bool,
                         registre: Any) -> dict[str, Any]:
    """Rassemble le résultat d'une conversion réussie en dictionnaire publiable."""
    texte, exacte = formater_fraction(resultat, chiffres)
    affine = (est_affine(source) or est_affine(cible)) and not ecart
    rapport: dict[str, Any] = {
        "entree": entree, "valeur_source": formater_fraction(valeur, chiffres)[0],
        "unite_source": source.texte, "unite_cible": cible.texte,
        "valeur": en_flottant(resultat), "valeur_texte": texte, "exacte": exacte,
        "fraction": str(resultat), "affine": affine,
        "facteur": None if affine else str(source.facteur / cible.facteur),
        "dimension": ecrire_dimension(source.dimension, lettres=True),
        "dimension_si": ecrire_dimension(source.dimension, lettres=False),
        "unites_nommees": unites_nommees_de(source.dimension),
        "notes": list(dict.fromkeys(notes)),
        "definitions": decrire_definitions(source, cible),
    }
    if registre is not None:
        rapport["controle_pint"] = controler_avec_pint(registre, valeur, source, cible, ecart, resultat)
    return rapport


def decrire_definitions(source: Expression, cible: Expression) -> list[dict[str, str]]:
    """Trace la définition et la source officielle de chaque unité employée."""
    vues: dict[str, dict[str, str]] = {}
    for composant in source.composants + cible.composants:
        unite = composant.unite
        vues.setdefault(unite.symbole, {
            "symbole": unite.symbole, "nom": unite.nom,
            "facteur_si": formater_fraction(unite.facteur, CHIFFRES_DEFAUT)[0],
            "decalage_k": formater_fraction(unite.decalage, CHIFFRES_DEFAUT)[0], "source": unite.source,
        })
    return list(vues.values())


def regrouper_quantites(brutes: Sequence[str]) -> list[str]:
    """Recolle « 3 » « km » passés sans guillemets en une seule quantité « 3 km »."""
    regroupees: list[str] = []
    for brute in brutes:
        precedente = regroupees[-1] if regroupees else None
        seul_nombre = precedente is not None and MOTIF_QUANTITE.match(precedente) is not None \
            and not MOTIF_QUANTITE.match(precedente).group(2)
        if seul_nombre and MOTIF_QUANTITE.match(brute) is None:
            regroupees[-1] = f"{precedente} {brute}"
        else:
            regroupees.append(brute)
    return regroupees


def traiter_entrees(entrees: Sequence[str], vers: str | None, ecart: bool, chiffres: int,
                    registre: Any) -> tuple[list[dict[str, Any]], int]:
    """Convertit chaque entrée ; rend les résultats et le code de sortie le plus grave."""
    resultats: list[dict[str, Any]] = []
    code = CODE_OK
    for entree in entrees:
        try:
            rapport = convertir(entree, vers, ecart, chiffres, registre)
            if rapport.get("controle_pint", {}).get("statut") == "desaccord":
                code = max(code, CODE_DEFAUT)
        except (ErreurUnite, ValueError, ZeroDivisionError) as exc:
            code_erreur = exc.code if isinstance(exc, ErreurUnite) else CODE_USAGE
            rapport = {"entree": entree, "erreur": str(exc), "code": code_erreur}
            code = max(code, code_erreur)
        resultats.append(rapport)
    return resultats, code


def extraire_contrat(doc: str) -> dict[str, str]:
    """Extrait du docstring les sections du contrat de mesure."""
    contrat: dict[str, str] = {}
    courant = None
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if tete in INTITULES and not ligne.startswith(" "):
            courant = tete
            contrat[courant] = ""
        elif courant:
            contrat[courant] = (contrat[courant] + " " + tete).strip()
    return contrat


def creer_registre_pint(moteur: str) -> Any:
    """Crée le registre pint si demandé et disponible ; annonce le repli sinon."""
    if moteur == "stdlib":
        return None
    if pint is None:
        print("convertir_unites : pint absent — repli stdlib seul (fractions exactes), sans contre-vérification",
              file=sys.stderr)
        return None
    return pint.UnitRegistry()


def construire_parseur() -> argparse.ArgumentParser:
    """Déclare l'interface en ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Convertit des quantités physiques en arithmétique exacte et vérifie la compatibilité "
                    "des dimensions (préfixes SI et binaires, unités composées, températures affines).",
        epilog='exemple : convertir_unites.py "90 km/h" --vers m/s --json   '
               '| convertir_unites.py "98.6 °F" --vers °C | convertir_unites.py "10 °C" --vers °F --ecart',
    )
    parseur.add_argument("quantites", nargs="+", metavar="QUANTITE",
                         help='quantité à convertir, nombre puis unité : "3 km", "1.5 MiB", "-40 °F"')
    parseur.add_argument("--vers", metavar="UNITE",
                         help="unité cible (défaut : unités SI de base de la dimension)")
    parseur.add_argument("--ecart", action="store_true",
                         help="lire les températures comme des écarts (10 °C → 18 °F) et non des températures absolues")
    parseur.add_argument("--chiffres", type=int, default=CHIFFRES_DEFAUT,
                         help=f"chiffres significatifs affichés pour un résultat non décimal exact (défaut {CHIFFRES_DEFAUT})")
    parseur.add_argument("--moteur", choices=("auto", "stdlib"), default="auto",
                         help="auto : contre-vérifie avec pint s'il est installé ; stdlib : jamais")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="racine de travail (sans effet sur les quantités ; acceptée pour l'uniformité du socle)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def afficher_humain(resultats: Sequence[Mapping[str, Any]]) -> None:
    """Affiche une ligne par conversion, et les notes."""
    for r in resultats:
        if "erreur" in r:
            continue
        exact = "" if r["exacte"] else f"  (≈, fraction exacte {r['fraction']})"
        nommees = f" ≡ {', '.join(r['unites_nommees'])}" if r["unites_nommees"] else ""
        print(f"{r['entree']} = {r['valeur_texte']} {r['unite_cible']}{exact}   [dimension {r['dimension']}{nommees}]")
        for note in r["notes"]:
            print(f"    note : {note}")
        controle = r.get("controle_pint")
        if controle:
            print(f"    pint : {controle['statut']}"
                  + (f", écart relatif {controle['ecart_relatif']:.2e}" if "ecart_relatif" in controle else ""))


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : analyse les arguments, convertit, publie."""
    args = construire_parseur().parse_args(argv)
    if args.chiffres < 1 or args.chiffres > 200:
        print("--chiffres doit être compris entre 1 et 200", file=sys.stderr)
        return CODE_USAGE
    registre = creer_registre_pint(args.moteur)
    quantites = regrouper_quantites(args.quantites)
    resultats, code = traiter_entrees(quantites, args.vers, args.ecart, args.chiffres, registre)
    for r in resultats:
        if "erreur" in r:
            print(f"convertir_unites : {r['entree']} : {r['erreur']}", file=sys.stderr)
    if args.json:
        rapport = {
            "outil": "convertir_unites",
            "moteur": "pint" if registre is not None else "stdlib",
            "version_moteur": getattr(pint, "__version__", None) if registre is not None else None,
            "denominateur": len(quantites),
            "examines": quantites[:50],
            "examines_tronques": len(quantites) > 50,
            "conversions": resultats,
            "contrat": extraire_contrat(__doc__ or ""),
        }
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
    else:
        afficher_humain(resultats)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
