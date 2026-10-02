"""Répare le JSON cassé qu'un LLM rend, et dit correction par correction ce qui a été changé.

Mesuré dans cette session (python corpus_reparer_json.py, bloc-notes de session) : sur 30
sorties de LLM cassées (clôtures de code, prose, virgules finales, guillemets simples, sortie
tronquée...), json.loads en refuse 30/30 ; ce réparateur en rend 30/30 égales à la valeur
attendue, json_repair 0.63.5 21/30 (il garde NaN, 'undefined' ou 'fal' tronqué en valeurs) ;
sur 85 fichiers .json valides trouvés dans ce bloc-notes, il ne change rien (85 « valide »).

QUESTION
    Ce JSON produit par un LLM peut-il être réparé, et par quelles corrections exactement ?
MESURE
    Un analyseur tolérant à descente récursive relit le texte octet par octet et réécrit
    un JSON canonique ; chaque écart à la grammaire JSON (RFC 8259) qu'il franchit est
    consigné (type, position, ligne, colonne). Le texte réparé est ensuite revalidé par
    json.loads en mode strict (NaN et Infinity refusés) : seul ce contrôle décide de
    « réparé ». Verdicts : valide (0 correction), repare, irreparable (avec la raison).
HYPOTHÈSES
    Le texte contient UN objet ou UN tableau JSON, éventuellement entouré de prose ou
    d'une clôture de code. Une sortie tronquée l'a été à la fin, pas au milieu.
    Dans une valeur, un guillemet ne ferme la chaîne que s'il est suivi de , : } ], d'une
    fin de ligne ou d'une autre chaîne complète : sinon il est pris pour un guillemet
    intérieur non échappé. C'est une heuristique, pas une certitude.
LIMITES
    Une chaîne tronquée est fermée telle quelle : la valeur obtenue est un préfixe de
    la vraie. Un membre tronqué avant sa valeur est supprimé. NaN et Infinity deviennent
    null (le JSON strict ne les connaît pas). Profondeur d'imbrication bornée à 200.
    Plusieurs valeurs JSON successives : seule la plus longue est gardée, les autres
    sont signalées. Une valeur en mot nu ({"etat": ok}) est déclarée irréparable.
    json_repair (facultatif) ne donne ni type ni position de correction.
CONTRE-EXEMPLES
    Constaté : {"p": "C:\\temp\\x"} est « réparé » en gardant \\t comme tabulation (le
    chemin Windows voulu est perdu) ; {"t": "il dit "non", puis part"} est déclaré
    irréparable, car le guillemet après « non » est suivi d'une virgule et passe pour
    une fin de chaîne ; il dit "non" puis part (sans virgule) est, lui, bien réparé.
INVOCATION
    {outil} --texte "{'ok': True, 'n': 1,}" --json
    {outil} {fichier} --json
DOMAINE
    Sorties textuelles de LLM censées être un objet ou un tableau JSON, de quelques
    octets à quelques dizaines de mégaoctets ; pas le JSON Lines (une valeur par ligne).
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import json_repair as _JSON_REPAIR
except ImportError:
    _JSON_REPAIR = None

RACINE = Path(__file__).resolve().parent

TITRE_QUESTION = "QUESTION"
TITRE_MESURE = "MESURE"
TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_LIMITES = "LIMITES"
TITRE_CONTRE_EXEMPLES = "CONTRE-EXEMPLES"
TITRE_INVOCATION = "INVOCATION"
TITRE_DOMAINE = "DOMAINE"
INTITULES = (TITRE_QUESTION, TITRE_MESURE, TITRE_HYPOTHESES, TITRE_LIMITES,
             TITRE_CONTRE_EXEMPLES, TITRE_INVOCATION, TITRE_DOMAINE)

MOTEUR_STDLIB = "stdlib"
MOTEUR_TIERS = "json_repair"
VERDICT_VALIDE = "valide"
VERDICT_REPARE = "repare"
VERDICT_IRREPARABLE = "irreparable"

CODE_RIEN = 0
CODE_DEFAUT = 1
CODE_USAGE = 2
CODE_VIDE = 3

PROFONDEUR_MAX = 200
CANDIDATS_MAX = 64
OCTETS_MAX_DEFAUT = 32 * 1024 * 1024
EXAMINES_MAX = 50
FICHIERS_MAX = 5000

GUILLEMETS_FERMANTS = {
    '"': '"',
    "'": "'",
    "“": "”“",
    "”": "”",
    "‘": "’",
    "’": "’",
    "«": "»",
}
ECHAPPEMENTS_JSON = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f",
                     "n": "\n", "r": "\r", "t": "\t"}
LITTERAUX = {
    "true": ("true", None), "false": ("false", None), "null": ("null", None),
    "True": ("true", "litteral_python"), "False": ("false", "litteral_python"),
    "None": ("null", "litteral_python"),
    "TRUE": ("true", "litteral_casse"), "FALSE": ("false", "litteral_casse"),
    "NULL": ("null", "litteral_casse"), "Null": ("null", "litteral_casse"),
    "undefined": ("null", "litteral_js"),
    "NaN": ("null", "nan_infinity"), "Infinity": ("null", "nan_infinity"),
    "inf": ("null", "nan_infinity"), "nan": ("null", "nan_infinity"),
}
RE_NOMBRE = re.compile(
    r"[+-]?(?:0[xX][0-9a-fA-F]+|(?:\d[\d_]*\.?[\d_]*|\.\d[\d_]*)(?:[eE][+-]?\d*)?)")
RE_NOMBRE_JSON = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?")
RE_IDENT = re.compile(r"[^\W\d][\w$-]*|\$[\w$-]*|\d+")
RE_CLOTURE = re.compile(r"```[A-Za-z0-9_+-]*")
RE_CHAINE_SUIVANTE = re.compile(r'"[^"\n]*"[ \t]*[:,\]}]')


class ErreurEntree(Exception):
    """Entrée illisible ou invalide : porte le code de sortie à rendre."""

    def __init__(self, message: str, code: int = CODE_USAGE) -> None:
        super().__init__(message)
        self.code = code


class _Irreparable(Exception):
    """L'analyseur tolérant ne sait pas franchir cet endroit du texte."""

    def __init__(self, message: str, position: int) -> None:
        super().__init__(message)
        self.position = position


class _Analyseur:
    """Analyseur JSON tolérant : lit depuis `debut`, réécrit, consigne les corrections."""

    def __init__(self, texte: str, debut: int) -> None:
        self.texte = texte
        self.n = len(texte)
        self.i = debut
        self.profondeur = 0
        self.corrections: list[dict[str, Any]] = []

    # -- outils de lecture -------------------------------------------------
    def fin(self) -> bool:
        return self.i >= self.n

    def car(self) -> str:
        return self.texte[self.i]

    def noter(self, type_: str, position: int, detail: str) -> None:
        self.corrections.append({"type": type_, "position": position, "detail": detail})

    def sauter_blancs(self) -> None:
        while not self.fin():
            c = self.car()
            if c in " \t\r\n":
                self.i += 1
            elif c in "\ufeff\u00a0\u200b":
                self.noter("blanc_invalide", self.i, f"blanc U+{ord(c):04X} hors JSON supprimé")
                self.i += 1
            elif self.texte.startswith("//", self.i) or c == "#":
                self.sauter_commentaire_ligne()
            elif self.texte.startswith("/*", self.i):
                self.sauter_commentaire_bloc()
            else:
                return

    def sauter_commentaire_ligne(self) -> None:
        debut = self.i
        fin = self.texte.find("\n", self.i)
        self.i = self.n if fin < 0 else fin
        extrait = self.texte[debut:self.i][:40]
        self.noter("commentaire", debut, f"commentaire supprimé : {extrait!r}")

    def sauter_commentaire_bloc(self) -> None:
        debut = self.i
        fin = self.texte.find("*/", self.i + 2)
        if fin < 0:
            self.i = self.n
            self.noter("commentaire", debut, "commentaire /* non fermé (sortie tronquée) supprimé")
            return
        self.i = fin + 2
        self.noter("commentaire", debut, f"commentaire supprimé : {self.texte[debut:self.i][:40]!r}")

    # -- valeurs ------------------------------------------------------------
    def valeur(self) -> str | None:
        """Lit une valeur ; None si le texte s'arrête avant qu'elle commence."""
        self.sauter_blancs()
        if self.fin():
            return None
        c = self.car()
        if c in "{[":
            return self.conteneur(c)
        if c in GUILLEMETS_FERMANTS:
            contenu, _ = self.chaine()
            return json.dumps(contenu, ensure_ascii=False)
        if c in "+-.0123456789":
            return self.nombre()
        if c.isalpha() or c == "_":
            return self.mot()
        raise _Irreparable(f"caractère {c!r} inattendu là où une valeur était attendue", self.i)

    def conteneur(self, ouvrant: str) -> str:
        self.profondeur += 1
        if self.profondeur > PROFONDEUR_MAX:
            raise _Irreparable(f"imbrication plus profonde que {PROFONDEUR_MAX}", self.i)
        fermant = "}" if ouvrant == "{" else "]"
        self.i += 1
        elements = self.elements(fermant)
        self.profondeur -= 1
        return ouvrant + ",".join(elements) + fermant

    def elements(self, fermant: str) -> list[str]:
        """Lit les éléments jusqu'au fermant ; gère virgules et troncature."""
        elements: list[str] = []
        virgule: int | None = None
        while True:
            self.sauter_blancs()
            if self.fin():
                self.clore_tronque(fermant, virgule)
                return elements
            c = self.car()
            if c in "}]":
                self.fermer(c, fermant, virgule)
                return elements
            if c == ",":
                virgule = self.virgule(elements, virgule)
                continue
            if elements and virgule is None:
                self.noter("virgule_manquante", self.i, "virgule insérée entre deux éléments")
            element = self.membre() if fermant == "}" else self.valeur()
            if element is None:
                self.noter("membre_incomplet", self.i, "élément tronqué avant sa valeur : supprimé")
                self.clore_tronque(fermant, None)
                return elements
            elements.append(element)
            virgule = None

    def virgule(self, elements: list[str], virgule: int | None) -> int | None:
        if not elements or virgule is not None:
            self.noter("virgule_superflue", self.i, "virgule sans élément : supprimée")
            self.i += 1
            return virgule
        self.i += 1
        return self.i - 1

    def fermer(self, lu: str, attendu: str, virgule: int | None) -> None:
        if virgule is not None:
            self.noter("virgule_finale", virgule, "virgule finale supprimée")
        if lu != attendu:
            self.noter("fermeture_corrigee", self.i, f"{lu!r} remplacé par {attendu!r}")
        self.i += 1

    def clore_tronque(self, fermant: str, virgule: int | None) -> None:
        if virgule is not None:
            self.noter("virgule_finale", virgule, "virgule finale supprimée (texte tronqué)")
        self.noter("fermeture_ajoutee", self.n, f"{fermant!r} ajouté : le texte s'arrête avant")

    def membre(self) -> str | None:
        cle = self.cle()
        if cle is None:
            return None
        self.sauter_blancs()
        if self.fin():
            return None
        self.deux_points()
        valeur = self.valeur()
        if valeur is None:
            return None
        return json.dumps(cle, ensure_ascii=False) + ":" + valeur

    def deux_points(self) -> None:
        c = self.car()
        if c == ":":
            self.i += 1
        elif c == "=":
            self.noter("deux_points_manquant", self.i, "'=' remplacé par ':'")
            self.i += 1
        elif c in ",}]":
            raise _Irreparable("clé sans valeur", self.i)
        else:
            self.noter("deux_points_manquant", self.i, "':' inséré après la clé")

    def cle(self) -> str | None:
        c = self.car()
        if c in GUILLEMETS_FERMANTS:
            contenu, fermee = self.chaine(cle=True)
            if not fermee:
                self.corrections.pop()
                return None
            return contenu
        trouve = RE_IDENT.match(self.texte, self.i)
        if trouve is None:
            raise _Irreparable(f"caractère {c!r} inattendu là où une clé était attendue", self.i)
        self.noter("cle_non_citee", self.i, f"clé {trouve.group()!r} mise entre guillemets")
        self.i = trouve.end()
        return trouve.group()

    # -- chaînes ------------------------------------------------------------
    def chaine(self, cle: bool = False) -> tuple[str, bool]:
        """Lit une chaîne ; rend (contenu, fermée normalement ?).

        Pour une clé, le premier guillemet fermant ferme toujours : l'heuristique du
        guillemet intérieur ne vaut que pour les valeurs."""
        ouvrant = self.car()
        fermants = GUILLEMETS_FERMANTS[ouvrant]
        if ouvrant == "'":
            self.noter("guillemets_simples", self.i, "chaîne entre apostrophes réécrite entre guillemets")
        elif ouvrant != '"':
            self.noter("guillemets_typographiques", self.i,
                       f"délimiteurs {ouvrant}…{fermants[0]} remplacés par des guillemets droits")
        self.i += 1
        morceaux: list[str] = []
        while not self.fin():
            c = self.car()
            if c == "\\":
                morceaux.append(self.echappement())
            elif c in fermants and (cle or self.fermeture_plausible()):
                self.i += 1
                return "".join(morceaux), True
            else:
                morceaux.append(self.caractere_de_chaine(c, fermants))
        self.noter("chaine_fermee", self.n, "chaîne tronquée : guillemet fermant ajouté")
        return "".join(morceaux), False

    def caractere_de_chaine(self, c: str, fermants: str) -> str:
        if c in fermants:
            quoi = "apostrophe intérieure gardée" if c in "'’" else "guillemet intérieur échappé"
            self.noter("guillemet_interne", self.i, f"{quoi} (suivi d'un caractère qui ne ferme pas)")
        elif ord(c) < 0x20:
            self.noter("controle_dans_chaine", self.i, f"caractère de contrôle U+{ord(c):04X} échappé")
        self.i += 1
        return c

    def fermeture_plausible(self) -> bool:
        """Un guillemet ferme la chaîne s'il est suivi de , : } ] d'un commentaire,
        d'une fin de ligne, de la fin du texte (blancs horizontaux sautés), ou d'une
        chaîne complète elle-même suivie de : , ] } (virgule manquante sur la ligne)."""
        j = self.i + 1
        while j < self.n and self.texte[j] in " \t":
            j += 1
        if j >= self.n or self.texte[j] in ",:}]\r\n#":
            return True
        if RE_CHAINE_SUIVANTE.match(self.texte, j):
            return True
        return self.texte.startswith(("//", "/*"), j)

    def echappement(self) -> str:
        debut = self.i
        if self.i + 1 >= self.n:
            self.i += 1
            return ""
        e = self.texte[self.i + 1]
        self.i += 2
        if e in "bf" and self.i < self.n and self.texte[self.i].isalnum():
            self.noter("echappement_ambigu", debut,
                       f"\\{e} suivi d'une lettre lu comme barre oblique littérale (chemin Windows ?)")
            return "\\" + e
        if e in ECHAPPEMENTS_JSON:
            return ECHAPPEMENTS_JSON[e]
        if e == "u":
            return self.echappement_unicode(debut)
        if e == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", self.texte[self.i:self.i + 2]):
            self.i += 2
            self.noter("echappement_invalide", debut, "\\x.. réécrit en \\u00..")
            return chr(int(self.texte[self.i - 2:self.i], 16))
        if e == "'":
            self.noter("echappement_invalide", debut, "\\' n'existe pas en JSON : apostrophe simple")
            return "'"
        if e == "\n":
            self.noter("echappement_invalide", debut, "continuation de ligne \\ supprimée")
            return ""
        self.noter("echappement_invalide", debut, f"\\{e} inconnu : barre oblique inverse conservée")
        return "\\" + e

    def echappement_unicode(self, debut: int) -> str:
        hexa = self.texte[self.i:self.i + 4]
        if not re.fullmatch(r"[0-9a-fA-F]{4}", hexa):
            if self.i + 4 > self.n:
                self.i = self.n
                return ""
            self.noter("echappement_invalide", debut, "\\u sans quatre chiffres hexadécimaux : conservé tel quel")
            return "\\u"
        self.i += 4
        point = int(hexa, 16)
        if 0xD800 <= point <= 0xDBFF and re.fullmatch(r"\\u[dD][c-fC-F][0-9a-fA-F]{2}",
                                                       self.texte[self.i:self.i + 6]):
            bas = int(self.texte[self.i + 2:self.i + 6], 16)
            self.i += 6
            return chr(0x10000 + ((point - 0xD800) << 10) + (bas - 0xDC00))
        if 0xD800 <= point <= 0xDFFF:
            self.noter("echappement_invalide", debut, f"demi-paire de substitution \\u{hexa} remplacée par U+FFFD")
            return "\ufffd"
        return chr(point)

    # -- nombres et mots ----------------------------------------------------
    def nombre(self) -> str | None:
        debut = self.i
        trouve = RE_NOMBRE.match(self.texte, self.i)
        if trouve is None:
            return self.signe_seul(debut)
        brut = trouve.group()
        self.i = trouve.end()
        if not self.fin() and (self.car().isalnum() or self.car() in "._"):
            raise _Irreparable(f"nombre {brut!r} suivi de {self.car()!r}", debut)
        texte = self.normaliser_nombre(brut, debut)
        if texte is not None and not RE_NOMBRE_JSON.fullmatch(texte):
            raise _Irreparable(f"nombre {brut!r} irrécupérable", debut)
        return texte

    def signe_seul(self, debut: int) -> str | None:
        signe = self.texte[debut]
        reste = self.texte[debut + 1:debut + 9]
        if signe in "+-" and reste.startswith("Infinity"):
            self.i = debut + 9
            self.noter("nan_infinity", debut, f"{signe}Infinity remplacé par null")
            return "null"
        if debut + 1 >= self.n:
            self.i = self.n
            return None
        raise _Irreparable(f"{signe!r} ne commence aucune valeur", debut)

    def normaliser_nombre(self, brut: str, debut: int) -> str | None:
        texte = brut.replace("_", "")
        if texte.lstrip("+-").lower().startswith("0x"):
            valeur = int(texte.replace("+", ""), 16)
            self.noter("nombre_corrige", debut, f"hexadécimal {brut} écrit {valeur}")
            return str(valeur)
        texte = self.reparer_forme_nombre(texte, debut)
        if texte is None:
            return None
        if texte != brut:
            self.noter("nombre_corrige", debut, f"{brut} écrit {texte}")
        return texte

    def reparer_forme_nombre(self, texte: str, debut: int) -> str | None:
        signe = "-" if texte.startswith("-") else ""
        corps = texte.lstrip("+-")
        if re.search(r"[eE][+-]?$", corps):
            self.noter("nombre_tronque", debut, "exposant sans chiffre supprimé")
            corps = re.sub(r"[eE][+-]?$", "", corps)
        decoupe = re.fullmatch(r"(\d*)(\.?)(\d*)([eE][+-]?\d+)?", corps)
        if decoupe is None or not (decoupe.group(1) or decoupe.group(3)):
            return None
        entier, point, decimales, exposant = decoupe.groups()
        entier = entier.lstrip("0") or "0"
        decimales = decimales or ("0" if point else "")
        return signe + entier + ("." + decimales if decimales else "") + (exposant or "")

    def mot(self) -> str:
        debut = self.i
        trouve = re.compile(r"[A-Za-z_][A-Za-z0-9_]*").match(self.texte, self.i)
        mot = trouve.group() if trouve else self.car()
        self.i = debut + len(mot)
        if mot in LITTERAUX:
            canon, type_ = LITTERAUX[mot]
            if type_ is not None:
                self.noter(type_, debut, f"{mot} remplacé par {canon}")
            return canon
        if self.fin():
            return self.completer_litteral(mot, debut)
        raise _Irreparable(f"mot nu {mot!r} : ni littéral JSON ni chaîne citée", debut)

    def completer_litteral(self, mot: str, debut: int) -> str:
        for canon in ("true", "false", "null"):
            if canon.startswith(mot.lower()):
                self.noter("litteral_tronque", debut, f"{mot!r} complété en {canon}")
                return canon
        for python, canon in (("True", "true"), ("False", "false"), ("None", "null")):
            if python.startswith(mot):
                self.noter("litteral_tronque", debut, f"{mot!r} complété en {canon}")
                return canon
        raise _Irreparable(f"mot nu {mot!r} en fin de texte", debut)


# -- orchestration d'un document ---------------------------------------------

def refuser_constante(nom: str) -> Any:
    """parse_constant de json.loads : NaN et Infinity ne sont pas du JSON strict."""
    raise ValueError(f"constante non JSON : {nom}")


def charger_strict(texte: str) -> tuple[Any, list[str]]:
    """json.loads strict ; rend la valeur et la liste des clés dupliquées."""
    doublons: list[str] = []

    def paires(liste: list[tuple[str, Any]]) -> dict[str, Any]:
        vus: dict[str, Any] = {}
        for cle, valeur in liste:
            if cle in vus:
                doublons.append(cle)
            vus[cle] = valeur
        return vus

    valeur = json.loads(texte, parse_constant=refuser_constante, object_pairs_hook=paires)
    return valeur, doublons


def essayer_valide(texte: str) -> tuple[Any, list[str]] | None:
    try:
        return charger_strict(texte)
    except (ValueError, RecursionError):
        return None


def candidats(texte: str) -> list[int]:
    """Positions des { et [ qui peuvent commencer la valeur JSON."""
    return [m.start() for m in re.finditer(r"[\[{]", texte)]


def analyser_depuis(texte: str, debut: int) -> tuple[str, int, list[dict[str, Any]]]:
    analyseur = _Analyseur(texte, debut)
    sortie = analyseur.valeur()
    if sortie is None:
        raise _Irreparable("texte vide après l'ouvrant", debut)
    return sortie, analyseur.i, analyseur.corrections


def meilleure_lecture(texte: str) -> tuple[tuple[str, int, int, list[dict[str, Any]]] | None, _Irreparable | None]:
    """Essaie chaque ouvrant ; garde la lecture couvrant le plus de texte.

    Une lecture réussie ENCLAVÉE dans une lecture échouée (ouvrant avant elle, échec
    après sa fin) n'est qu'un fragment de la vraie valeur : elle est écartée."""
    meilleure = None
    echecs: list[_Irreparable] = []
    couvert = -1
    for debut in candidats(texte)[:CANDIDATS_MAX]:
        if debut < couvert:
            continue
        try:
            sortie, fin, corrections = analyser_depuis(texte, debut)
        except _Irreparable as erreur:
            echecs.append(erreur)
            continue
        except RecursionError:
            echecs.append(_Irreparable("imbrication trop profonde pour la pile Python", debut))
            continue
        couvert = fin
        if any(e.position >= fin for e in echecs):
            continue
        if meilleure is None or fin - debut > meilleure[2] - meilleure[1]:
            meilleure = (sortie, debut, fin, corrections)
    return meilleure, (echecs[0] if echecs else None)


def corrections_bords(texte: str, debut: int, fin: int) -> list[dict[str, Any]]:
    """Clôtures de code et prose avant/après la valeur retenue."""
    resultat: list[dict[str, Any]] = []
    for depart, morceau, cote in ((0, texte[:debut], "avant"), (fin, texte[fin:], "apres")):
        resultat.extend(classer_bord(depart, morceau, cote))
    return resultat


def classer_bord(depart: int, morceau: str, cote: str) -> list[dict[str, Any]]:
    trouves: list[dict[str, Any]] = []
    for cloture in RE_CLOTURE.finditer(morceau):
        trouves.append({"type": "cloture_code", "position": depart + cloture.start(),
                        "detail": f"clôture {cloture.group()!r} supprimée"})
    reste = RE_CLOTURE.sub("", morceau)
    if reste.strip():
        decalage = len(morceau) - len(morceau.lstrip())
        extrait = reste.strip()[:50]
        if cote == "apres" and reste.strip()[:1] in "{[":
            trouves.append({"type": "valeur_supplementaire", "position": depart + decalage,
                            "detail": f"autre valeur JSON après la première, ignorée : {extrait!r}"})
        elif cote == "apres" and not reste.strip().strip("}]"):
            trouves.append({"type": "fermeture_superflue", "position": depart + decalage,
                            "detail": f"fermants en trop supprimés : {extrait!r}"})
        else:
            trouves.append({"type": f"prose_{cote}", "position": depart + decalage,
                            "detail": f"texte {cote} la valeur JSON supprimé : {extrait!r}"})
    return trouves


def situer(texte: str, corrections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ajoute ligne et colonne (comptées depuis 1) à chaque correction."""
    debuts = [0] + [m.end() for m in re.finditer("\n", texte)]
    for correction in corrections:
        ligne = bisect.bisect_right(debuts, correction["position"]) - 1
        correction["ligne"] = ligne + 1
        correction["colonne"] = correction["position"] - debuts[ligne] + 1
    return sorted(corrections, key=lambda c: c["position"])


def reparer_stdlib(texte: str) -> dict[str, Any]:
    """Répare avec l'analyseur tolérant ; rend verdict, corrections, texte réparé."""
    deja = essayer_valide(texte)
    if deja is not None:
        return {"verdict": VERDICT_VALIDE, "corrections": [], "texte_repare": texte.strip(),
                "doublons": deja[1], "valeur": deja[0]}
    meilleure, erreur = meilleure_lecture(texte)
    if meilleure is None:
        return irreparable(texte, erreur)
    sortie, debut, fin, corrections = meilleure
    corrections = corrections + corrections_bords(texte, debut, fin)
    try:
        valeur, doublons = charger_strict(sortie)
    except (ValueError, RecursionError) as exc:
        return {"verdict": VERDICT_IRREPARABLE, "corrections": situer(texte, corrections),
                "raison": f"le texte réécrit ne passe pas json.loads : {exc}"}
    return {"verdict": VERDICT_REPARE, "corrections": situer(texte, corrections),
            "texte_repare": sortie, "doublons": doublons, "valeur": valeur}


def irreparable(texte: str, erreur: _Irreparable | None) -> dict[str, Any]:
    if erreur is None:
        raison = "aucun objet { } ni tableau [ ] JSON dans le texte"
        return {"verdict": VERDICT_IRREPARABLE, "corrections": [], "raison": raison}
    position = situer(texte, [{"position": erreur.position}])[0]
    raison = f"{erreur} (ligne {position['ligne']}, colonne {position['colonne']})"
    return {"verdict": VERDICT_IRREPARABLE, "corrections": [], "raison": raison,
            "position_echec": erreur.position}


def reparer_tiers(texte: str) -> dict[str, Any]:
    """Répare avec json_repair ; ses corrections n'ont ni type précis ni position."""
    objet, journal = _JSON_REPAIR.repair_json(texte, return_objects=True, logging=True)
    if objet == "" or objet is None:
        return {"verdict": VERDICT_IRREPARABLE, "corrections": [],
                "raison": "json_repair ne trouve aucune valeur JSON"}
    corrections = [{"type": "json_repair", "position": max(texte.find(e.get("context", "")), 0),
                    "detail": e.get("text", "")} for e in journal]
    try:
        sortie = json.dumps(objet, ensure_ascii=False, allow_nan=False)
        valeur, doublons = charger_strict(sortie)
    except ValueError as exc:
        return {"verdict": VERDICT_IRREPARABLE, "corrections": situer(texte, corrections),
                "raison": f"json_repair rend une valeur hors JSON strict : {exc}"}
    return {"verdict": VERDICT_REPARE, "corrections": situer(texte, corrections),
            "texte_repare": sortie, "doublons": doublons, "valeur": valeur}


def comparer_moteurs(texte: str, principal: dict[str, Any]) -> dict[str, Any]:
    """Second avis de json_repair sur le même texte."""
    tiers = reparer_tiers(texte)
    accord = (tiers["verdict"] != VERDICT_IRREPARABLE and principal["verdict"] != VERDICT_IRREPARABLE
              and tiers.get("valeur") == principal.get("valeur"))
    return {"moteur": MOTEUR_TIERS, "verdict": tiers["verdict"], "accord_valeur": accord,
            "texte_repare": tiers.get("texte_repare"), "nb_entrees_journal": len(tiers["corrections"])}


def traiter_document(nom: str, texte: str, moteur: str, comparer: bool) -> dict[str, Any]:
    if moteur == MOTEUR_TIERS:
        resultat = reparer_tiers(texte)
        utilise = MOTEUR_TIERS
    else:
        resultat = reparer_stdlib(texte)
        utilise = MOTEUR_STDLIB
        if resultat["verdict"] == VERDICT_IRREPARABLE and moteur == "auto" and _JSON_REPAIR is not None:
            secours = reparer_tiers(texte)
            if secours["verdict"] == VERDICT_REPARE:
                secours["raison_stdlib"] = resultat.get("raison")
                resultat, utilise = secours, MOTEUR_TIERS
    resultat.update({"nom": nom, "moteur": utilise, "nb_corrections": len(resultat["corrections"]),
                     "octets": len(texte.encode("utf-8", "surrogatepass"))})
    if comparer and _JSON_REPAIR is not None and utilise == MOTEUR_STDLIB:
        resultat["comparaison"] = comparer_moteurs(texte, resultat)
    resultat.pop("valeur", None)
    resultat.setdefault("texte_repare", None)
    resultat.setdefault("doublons", [])
    resultat.setdefault("raison", None)
    return resultat


# -- entrées -----------------------------------------------------------------

def decoder(octets: bytes, nom: str) -> str:
    """Décode en UTF-8 (BOM toléré) ; refuse le binaire."""
    if b"\x00" in octets[:65536]:
        raise ErreurEntree(f"{nom} : fichier binaire (octet NUL), pas du texte JSON")
    try:
        return octets.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{nom} : pas de l'UTF-8 valide (octet {exc.start}) ; "
                           "réencoder d'abord (voir deviner_encodage)") from exc


def lire_borne(chemin: Path, octets_max: int) -> bytes:
    with chemin.open("rb") as flux:
        octets = flux.read(octets_max + 1)
    if len(octets) > octets_max:
        raise ErreurEntree(f"{chemin} dépasse {octets_max} octets (--max-octets)")
    return octets


def lister_fichiers(dossier: Path, motif: str) -> list[Path]:
    fichiers = sorted(p for p in dossier.rglob(motif) if p.is_file()
                      and not any(part.startswith(".") for part in p.relative_to(dossier).parts))
    if len(fichiers) > FICHIERS_MAX:
        print(f"{len(fichiers)} fichiers : seuls les {FICHIERS_MAX} premiers sont examinés",
              file=sys.stderr)
    return fichiers[:FICHIERS_MAX]


def collecter_entrees(args: argparse.Namespace, base: Path) -> list[tuple[str, str]]:
    """Rend la liste (nom, texte) des documents à examiner."""
    if args.texte is not None:
        return [("<texte>", decoder(os.fsencode(args.texte), "<texte>"))]
    if args.chemin is None:
        raise ErreurEntree("rien à lire : donner un CHEMIN, '-' (entrée standard) ou --texte")
    if args.chemin == "-":
        octets = sys.stdin.buffer.read(args.max_octets + 1)
        if len(octets) > args.max_octets:
            raise ErreurEntree(f"entrée standard plus longue que {args.max_octets} octets (--max-octets)")
        return [("<stdin>", decoder(octets, "<stdin>"))]
    chemin = Path(args.chemin)
    chemin = chemin if chemin.is_absolute() else base / chemin
    if not chemin.exists():
        raise ErreurEntree(f"{chemin} : chemin inexistant")
    if chemin.is_dir():
        return [(str(p), decoder(lire_borne(p, args.max_octets), str(p)))
                for p in lister_fichiers(chemin, args.motif)]
    return [(str(chemin), decoder(lire_borne(chemin, args.max_octets), str(chemin)))]


def choisir_moteur(demande: str) -> str:
    if demande == MOTEUR_TIERS and _JSON_REPAIR is None:
        raise ErreurEntree("--moteur json_repair demandé mais le paquet json-repair est absent")
    if demande == "auto" and _JSON_REPAIR is None:
        print("json_repair absent : moteur stdlib seul (analyseur tolérant), sans second avis "
              "ni secours tiers", file=sys.stderr)
    return demande


# -- sorties -----------------------------------------------------------------

def extraire_contrat(doc: str) -> dict[str, str]:
    """Découpe la docstring du module selon ses intitulés."""
    sections: dict[str, list[str]] = {}
    courant: str | None = None
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if tete in INTITULES and not ligne.startswith((" ", "\t")):
            courant = tete
            sections[courant] = []
        elif courant is not None and tete:
            sections[courant].append(tete)
    return {cle.lower().replace("è", "e").replace("-", "_"): " ".join(v) for cle, v in sections.items()}


def bilan(documents: list[dict[str, Any]]) -> dict[str, int]:
    compte = {VERDICT_VALIDE: 0, VERDICT_REPARE: 0, VERDICT_IRREPARABLE: 0}
    for document in documents:
        compte[document["verdict"]] += 1
    return compte


def construire_rapport(documents: list[dict[str, Any]], base: Path) -> dict[str, Any]:
    noms = [d["nom"] for d in documents]
    moteurs = sorted({d["moteur"] for d in documents}) or [MOTEUR_STDLIB]
    return {
        "denominateur": len(documents),
        "examines": noms[:EXAMINES_MAX],
        "examines_tronques": len(noms) > EXAMINES_MAX,
        "moteur": moteurs[0] if len(moteurs) == 1 else "+".join(moteurs),
        "json_repair_disponible": _JSON_REPAIR is not None,
        "racine": str(base),
        "bilan": bilan(documents),
        "documents": documents,
        "contrat": extraire_contrat(__doc__ or ""),
    }


def afficher_humain(rapport: dict[str, Any]) -> None:
    for document in rapport["documents"]:
        sys.stdout.write(f"{document['nom']} : {document['verdict'].upper()} "
                         f"({document['nb_corrections']} correction(s), moteur {document['moteur']})\n")
        for c in document["corrections"]:
            lieu = f"l.{c['ligne']}:{c['colonne']}"
            sys.stdout.write(f"  {lieu:<12} {c['type']:<24} {c['detail']}\n")
        if document.get("raison"):
            sys.stdout.write(f"  raison : {document['raison']}\n")
        for cle in document.get("doublons", []):
            sys.stdout.write(f"  attention : clé dupliquée {cle!r} (json.loads garde la dernière)\n")
        if "comparaison" in document:
            comp = document["comparaison"]
            sys.stdout.write(f"  second avis json_repair : {comp['verdict']}, "
                             f"même valeur : {'oui' if comp['accord_valeur'] else 'NON'}\n")
        if document.get("texte_repare") and document["verdict"] != VERDICT_VALIDE:
            sys.stdout.write(f"  réparé : {document['texte_repare'][:2000]}\n")
    b = rapport["bilan"]
    sys.stdout.write(f"{rapport['denominateur']} document(s) : {b[VERDICT_VALIDE]} valide(s), "
                     f"{b[VERDICT_REPARE]} réparé(s), {b[VERDICT_IRREPARABLE]} irréparable(s)\n")


def ecrire_sortie(chemin: Path, documents: list[dict[str, Any]]) -> None:
    if len(documents) != 1:
        raise ErreurEntree("--sortie n'accepte qu'un seul document en entrée")
    texte = documents[0].get("texte_repare")
    if texte is None:
        print("document irréparable : rien n'est écrit dans --sortie", file=sys.stderr)
        return
    chemin.write_text(texte + "\n", encoding="utf-8")


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        description="Répare le JSON cassé produit par un LLM (clôtures de code, prose, virgules "
                    "finales, guillemets simples, clés non citées, commentaires, True/None, NaN, "
                    "guillemets typographiques, sortie tronquée) et liste chaque correction.",
        epilog="Exemple : python reparer_json.py reponse_llm.txt --json\n"
               "          python reparer_json.py --texte \"{'a': 1,}\" --sortie propre.json\n"
               "Codes : 0 déjà valide ; 1 réparé ou irréparable (voir verdict) ; 2 usage ; "
               "3 rien à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parseur.add_argument("chemin", nargs="?", help="fichier à réparer, dossier (fichiers --motif) ou '-'")
    parseur.add_argument("--texte", help="texte JSON donné en ligne (au lieu d'un chemin)")
    parseur.add_argument("--motif", default="*.json", help="motif des fichiers d'un dossier (défaut *.json)")
    parseur.add_argument("--moteur", choices=("auto", MOTEUR_STDLIB, MOTEUR_TIERS), default="auto",
                         help="auto : stdlib, puis json_repair en secours s'il est installé")
    parseur.add_argument("--comparer", action="store_true", help="second avis json_repair (si installé)")
    parseur.add_argument("--sortie", type=Path, help="écrit le JSON réparé dans ce fichier")
    parseur.add_argument("--max-octets", type=int, default=OCTETS_MAX_DEFAUT, help="taille maximale lue")
    parseur.add_argument("--racine", type=Path,
                         help=f"base des chemins relatifs (défaut : dossier courant ; outil : {RACINE.name})")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def code_de_sortie(documents: list[dict[str, Any]]) -> int:
    return CODE_RIEN if all(d["verdict"] == VERDICT_VALIDE for d in documents) else CODE_DEFAUT


def main(argv: list[str] | None = None) -> int:
    args = construire_parseur().parse_args(argv)
    base = (args.racine if args.racine is not None else Path.cwd()).resolve()
    try:
        moteur = choisir_moteur(args.moteur)
        entrees = [(nom, texte) for nom, texte in collecter_entrees(args, base) if texte.strip()]
        documents = [traiter_document(nom, texte, moteur, args.comparer) for nom, texte in entrees]
        if documents and args.sortie is not None:
            ecrire_sortie(args.sortie if args.sortie.is_absolute() else base / args.sortie, documents)
    except ErreurEntree as erreur:
        print(f"erreur : {erreur}", file=sys.stderr)
        return erreur.code
    except OSError as erreur:
        print(f"erreur de lecture/écriture : {erreur}", file=sys.stderr)
        return CODE_USAGE
    rapport = construire_rapport(documents, base)
    if args.json:
        sys.stdout.write(json.dumps(rapport, ensure_ascii=False, indent=2) + "\n")
    else:
        afficher_humain(rapport)
    if not documents:
        print("dénominateur nul : rien à examiner (aucun document non vide)", file=sys.stderr)
        return CODE_VIDE
    return code_de_sortie(documents)


if __name__ == "__main__":
    raise SystemExit(main())
