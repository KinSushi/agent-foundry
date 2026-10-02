"""JMESPath est la langue des options --query d'AWS CLI et d'Azure CLI, mais
l'interpréteur propre n'a pas jmespath (mesuré : `python3.14 -c 'import jmespath'`
rend ModuleNotFoundError) et jq, présent sur cette machine, parle une autre
langue. L'expression la plus naturelle pour un agent, `a > 3`, est une erreur de
syntaxe (mesuré, jmespath 1.1.0 : ParseError à la colonne 4 ; il faut a > `3`),
et jmespath 1.1.0 lève TypeError sur `a < b` appliqué à {"a": "x", "b": 1}
(mesuré) là où la spécification rend null.

QUESTION
    Que vaut cette expression JMESPath sur ce document JSON (ou sur chaque ligne
    de ce fichier JSON Lines) ?
MESURE
    Évaluation de l'expression par la bibliothèque jmespath si elle est installée,
    sinon par un moteur stdlib qui implémente un sous-ensemble documenté de la
    grammaire avec la même précédence (analyseur de Pratt) et la même sémantique :
    champs a.b et "champ quoté", index [0] et [-1], tranches [1:3] et [::2],
    projections [*], .*, aplatissement [], filtres [?k == 'v'] avec == != < <= >
    >=, &&, ||, !, parenthèses, tube |, nœud courant @, littéraux 'brut' et
    `json`, fonction length(). Toute autre construction (sélections multiples
    [a, b] et {k: v}, &expr, autres fonctions) est REFUSÉE (code 2), jamais
    devinée. --moteur comparer évalue avec les deux moteurs et dit s'ils
    s'accordent.
HYPOTHÈSES
    L'entrée est du JSON (RFC 8259) ou du JSON Lines (un document par ligne, choisi
    par l'extension .jsonl/.ndjson ou --jsonl). Les constantes non standard NaN et
    Infinity sont acceptées, avec un avertissement.
LIMITES
    Le moteur stdlib ne couvre pas toute la spécification (voir MESURE). Sur une
    comparaison d'ordre entre une chaîne et un nombre, il rend null comme la
    spécification, là où jmespath 1.1.0 lève TypeError : les deux moteurs
    divergent sur ce cas. Le document est chargé entier en mémoire (plafond
    --max-octets).
CONTRE-EXEMPLES
    Pour l'expression `a == b` sur {"a": 1, "b": 1.0}, l'outil rend true (comme
    jmespath) alors que les deux valeurs s'écrivent différemment : l'égalité est
    numérique, pas textuelle (vérifié avec les deux moteurs).
INVOCATION
    {outil} "commandes[?montant > `100`].client" --texte '{"commandes": [{"client": "Ana", "montant": 120}, {"client": "Bo", "montant": 80}]}' --json
DOMAINE
    Requêtes JMESPath sur des documents JSON ou JSON Lines tenant en mémoire :
    extraire, filtrer, compter, vérifier une expression --query avant de la
    confier à une ligne de commande d'exploitation.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import jmespath  # type: ignore[import-not-found]
    from jmespath import exceptions as exceptions_jmespath  # type: ignore[import-not-found]
except ImportError:
    jmespath = None
    exceptions_jmespath = None

RACINE = Path(__file__).resolve().parent

INTITULE_QUESTION = "QUESTION"
INTITULE_MESURE = "MESURE"
INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_LIMITES = "LIMITES"
INTITULE_CONTRE_EXEMPLES = "CONTRE-EXEMPLES"
INTITULE_INVOCATION = "INVOCATION"
INTITULE_DOMAINE = "DOMAINE"
INTITULE_REFUSEE = "REFUSÉE"
INTITULES = (
    INTITULE_QUESTION, INTITULE_MESURE, INTITULE_HYPOTHESES, INTITULE_LIMITES,
    INTITULE_CONTRE_EXEMPLES, INTITULE_INVOCATION, INTITULE_DOMAINE,
)

PUISSANCES = MappingProxyType({
    "eof": 0, "unquoted_identifier": 0, "quoted_identifier": 0, "literal": 0,
    "rbracket": 0, "rparen": 0, "comma": 0, "rbrace": 0, "number": 0,
    "current": 0, "expref": 0, "colon": 0, "pipe": 1, "or": 2, "and": 3,
    "eq": 5, "gt": 5, "lt": 5, "gte": 5, "lte": 5, "ne": 5, "flatten": 9,
    "star": 20, "filter": 21, "dot": 40, "not": 45, "lbrace": 50,
    "lbracket": 55, "lparen": 60,
})
ARRET_PROJECTION = 10
JETONS_SIMPLES = MappingProxyType({
    ".": "dot", "*": "star", "]": "rbracket", ",": "comma", ":": "colon",
    "@": "current", "(": "lparen", ")": "rparen", "{": "lbrace", "}": "rbrace",
})
DEBUT_IDENTIFIANT = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
SUITE_IDENTIFIANT = DEBUT_IDENTIFIANT | frozenset("0123456789")
CHIFFRES = frozenset("0123456789")
BLANCS = frozenset(" \t\n\r")
FONCTIONS_JMESPATH = frozenset({
    "abs", "avg", "ceil", "contains", "ends_with", "floor", "join", "keys", "length",
    "map", "max", "max_by", "merge", "min", "min_by", "not_null", "reverse", "sort",
    "sort_by", "starts_with", "sum", "to_array", "to_number", "to_string", "type", "values",
})
EXTENSIONS_LIGNES = (".jsonl", ".ndjson")
LIMITE_EXAMINES = 200


class ErreurRequete(Exception):
    """Expression ou entrée refusée ; `code` est le code de sortie proposé."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Jeton:
    """Un jeton du lexique JMESPath."""

    genre: str
    valeur: Any
    position: int


@dataclass
class Noeud:
    """Nœud de l'arbre syntaxique (mêmes types que la référence jmespath)."""

    genre: str
    enfants: list[Any] = field(default_factory=list)
    valeur: Any = None


# --------------------------------------------------------------------------- #
# Lexique

class Lexeur:
    """Découpe une expression en jetons, à l'identique de la référence."""

    def __init__(self, expression: str) -> None:
        if not expression:
            raise ErreurRequete("expression vide")
        self.texte = expression
        self.position = 0

    def courant(self) -> str | None:
        """Caractère sous le curseur, None en fin d'expression."""
        return self.texte[self.position] if self.position < len(self.texte) else None

    def suivant(self) -> str | None:
        """Avance d'un caractère et rend le nouveau courant."""
        self.position += 1
        return self.courant()

    def jetons(self) -> list[Jeton]:
        """Liste complète des jetons, terminée par eof."""
        resultat = []
        while self.courant() is not None:
            jeton = self.jeton_suivant()
            if jeton is not None:
                resultat.append(jeton)
        resultat.append(Jeton("eof", "", len(self.texte)))
        return resultat

    def jeton_suivant(self) -> Jeton | None:
        """Lit un jeton (None pour un blanc)."""
        car, debut = self.courant(), self.position
        if car in JETONS_SIMPLES:
            self.suivant()
            return Jeton(JETONS_SIMPLES[car], car, debut)
        if car in DEBUT_IDENTIFIANT:
            return self.identifiant()
        if car in BLANCS:
            self.suivant()
            return None
        if car in CHIFFRES or car == "-":
            return self.nombre()
        return self.jeton_compose(car, debut)

    def jeton_compose(self, car: str | None, debut: int) -> Jeton:
        """Crochets, littéraux, opérateurs à un ou deux caractères."""
        if car == "[":
            suite = self.suivant()
            if suite in ("]", "?"):
                self.suivant()
                return Jeton("flatten" if suite == "]" else "filter", "[" + suite, debut)
            return Jeton("lbracket", "[", debut)
        if car == "'":
            return Jeton("literal", self.jusqua("'").replace("\\'", "'"), debut)
        if car == "`":
            return Jeton("literal", self.litteral_json(debut), debut)
        if car == '"':
            return Jeton("quoted_identifier", self.identifiant_quote(debut), debut)
        doubles = {"|": ("|", "or", "pipe"), "&": ("&", "and", "expref"), "<": ("=", "lte", "lt"),
                   ">": ("=", "gte", "gt"), "!": ("=", "ne", "not"), "=": ("=", "eq", None)}
        if car in doubles:
            attendu, double, simple = doubles[car]
            if self.suivant() == attendu:
                self.suivant()
                return Jeton(double, car + attendu, debut)
            if simple is not None:
                return Jeton(simple, car, debut)
        raise ErreurRequete(f"syntaxe : caractère inattendu {car!r} en position {debut}")

    def identifiant(self) -> Jeton:
        """Identifiant non quoté."""
        debut = self.position
        while self.suivant() in SUITE_IDENTIFIANT:
            pass
        return Jeton("unquoted_identifier", self.texte[debut:self.position], debut)

    def nombre(self) -> Jeton:
        """Entier, éventuellement négatif."""
        debut = self.position
        while self.suivant() in CHIFFRES:
            pass
        brut = self.texte[debut:self.position]
        if brut == "-":
            raise ErreurRequete(f"syntaxe : « - » isolé en position {debut}")
        return Jeton("number", int(brut), debut)

    def jusqua(self, delimiteur: str) -> str:
        """Contenu jusqu'au délimiteur ; une barre oblique inverse protège le suivant."""
        debut = self.position
        tampon = []
        car = self.suivant()
        while car != delimiteur:
            if car == "\\":
                tampon.append("\\")
                car = self.suivant()
            if car is None:
                raise ErreurRequete(f"syntaxe : délimiteur {delimiteur} non refermé (position {debut})")
            tampon.append(car)
            car = self.suivant()
        self.suivant()
        return "".join(tampon)

    def litteral_json(self, debut: int) -> Any:
        """`json` ; à défaut, ancienne forme `texte` lue comme une chaîne."""
        brut = self.jusqua("`").replace("\\`", "`")
        try:
            return json.loads(brut)
        except ValueError:
            try:
                return json.loads('"%s"' % brut.lstrip())
            except ValueError as exc:
                raise ErreurRequete(f"syntaxe : littéral JSON invalide `{brut}` (position {debut})") from exc

    def identifiant_quote(self, debut: int) -> str:
        """« identifiant quoté », décodé comme une chaîne JSON."""
        brut = '"' + self.jusqua('"') + '"'
        try:
            return json.loads(brut)
        except ValueError as exc:
            raise ErreurRequete(f"syntaxe : identifiant quoté invalide {brut} (position {debut})") from exc


# --------------------------------------------------------------------------- #
# Syntaxe (analyseur de Pratt, même table de puissances que la référence)

class Analyseur:
    """Construit l'arbre ; refuse explicitement ce que le moteur stdlib ne sait pas."""

    def __init__(self, expression: str) -> None:
        self.jetons = Lexeur(expression).jetons()
        self.index = 0

    def analyser(self) -> Noeud:
        """Arbre de l'expression complète."""
        arbre = self.expression(0)
        if self.genre() != "eof":
            jeton = self.jetons[self.index]
            raise ErreurRequete(f"syntaxe : jeton inattendu {jeton.valeur!r} en position {jeton.position}")
        return arbre

    def genre(self, decalage: int = 0) -> str:
        """Genre du jeton à `decalage` du curseur."""
        return self.jetons[min(self.index + decalage, len(self.jetons) - 1)].genre

    def avancer(self) -> Jeton:
        """Consomme le jeton courant."""
        jeton = self.jetons[self.index]
        self.index = min(self.index + 1, len(self.jetons) - 1)
        return jeton

    def exiger(self, genre: str) -> None:
        """Consomme un jeton du genre attendu, sinon erreur de syntaxe."""
        if self.genre() != genre:
            jeton = self.jetons[self.index]
            raise ErreurRequete(f"syntaxe : attendu {genre}, trouvé {jeton.genre} {jeton.valeur!r} "
                                f"en position {jeton.position}")
        self.avancer()

    def expression(self, puissance: int) -> Noeud:
        """Cœur de Pratt : préfixe, puis infixes plus liants que `puissance`."""
        gauche = self.prefixe(self.avancer())
        while puissance < PUISSANCES[self.genre()]:
            gauche = self.infixe(self.avancer(), gauche)
        return gauche

    def refuser(self, construction: str, jeton: Jeton) -> ErreurRequete:
        """Erreur de refus : construction valide que le moteur stdlib ne sait pas."""
        return ErreurRequete(
            f"construction {INTITULE_REFUSEE.lower()} par le moteur stdlib : {construction} (position "
            f"{jeton.position}) ; installer jmespath ou réécrire l'expression")

    def prefixe(self, jeton: Jeton) -> Noeud:
        """Rôle d'un jeton en tête d'expression (nud)."""
        genre = jeton.genre
        if genre == "literal":
            return Noeud("literal", valeur=jeton.valeur)
        if genre in ("unquoted_identifier", "quoted_identifier"):
            if genre == "quoted_identifier" and self.genre() == "lparen":
                raise ErreurRequete("syntaxe : un identifiant quoté ne peut pas nommer une fonction")
            return Noeud("field", valeur=jeton.valeur)
        if genre == "star":
            droite = Noeud("identity") if self.genre() == "rbracket" else self.droite_projection(PUISSANCES["star"])
            return Noeud("value_projection", [Noeud("identity"), droite])
        if genre == "filter":
            return self.filtre(Noeud("identity"))
        if genre == "lparen":
            interieur = self.expression(0)
            self.exiger("rparen")
            return interieur
        if genre == "flatten":
            return Noeud("projection", [Noeud("flatten", [Noeud("identity")]),
                                        self.droite_projection(PUISSANCES["flatten"])])
        if genre == "not":
            return Noeud("not_expression", [self.expression(PUISSANCES["not"])])
        if genre == "lbracket":
            return self.crochet_prefixe(jeton)
        if genre == "current":
            return Noeud("current")
        return self.prefixe_refuse(jeton)

    def prefixe_refuse(self, jeton: Jeton) -> Noeud:
        """Jetons sans rôle préfixe connu du moteur stdlib."""
        if jeton.genre == "lbrace":
            raise self.refuser("sélection multiple {clé: expr}", jeton)
        if jeton.genre == "expref":
            raise self.refuser("référence d'expression &expr", jeton)
        if jeton.genre == "eof":
            raise ErreurRequete("syntaxe : expression incomplète")
        raise ErreurRequete(f"syntaxe : jeton {jeton.valeur!r} inattendu en position {jeton.position}")

    def crochet_prefixe(self, jeton: Jeton) -> Noeud:
        """[0], [1:2], [*] en tête ; [a, b] refusé."""
        if self.genre() in ("number", "colon"):
            return self.projeter_si_tranche(Noeud("identity"), self.index_ou_tranche())
        if self.genre() == "star" and self.genre(1) == "rbracket":
            self.avancer()
            self.avancer()
            return Noeud("projection", [Noeud("identity"), self.droite_projection(PUISSANCES["star"])])
        raise self.refuser("sélection multiple [expr, expr]", jeton)

    def infixe(self, jeton: Jeton, gauche: Noeud) -> Noeud:
        """Rôle d'un jeton après une expression (led)."""
        genre = jeton.genre
        if genre == "dot":
            return self.point(gauche)
        if genre in ("pipe", "or", "and"):
            noms = {"pipe": "pipe", "or": "or_expression", "and": "and_expression"}
            return Noeud(noms[genre], [gauche, self.expression(PUISSANCES[genre])])
        if genre in ("eq", "ne", "lt", "lte", "gt", "gte"):
            return Noeud("comparator", [gauche, self.expression(PUISSANCES[genre])], genre)
        if genre == "filter":
            return self.filtre(gauche)
        if genre == "flatten":
            return Noeud("projection", [Noeud("flatten", [gauche]), self.droite_projection(PUISSANCES["flatten"])])
        if genre == "lbracket":
            return self.crochet_infixe(gauche)
        if genre == "lparen":
            return self.fonction(gauche, jeton)
        raise ErreurRequete(f"syntaxe : jeton {jeton.valeur!r} inattendu en position {jeton.position}")

    def point(self, gauche: Noeud) -> Noeud:
        """a.b, a.*"""
        if self.genre() == "star":
            self.avancer()
            return Noeud("value_projection", [gauche, self.droite_projection(PUISSANCES["dot"])])
        droite = self.droite_point(PUISSANCES["dot"])
        if gauche.genre == "subexpression":
            gauche.enfants.append(droite)
            return gauche
        return Noeud("subexpression", [gauche, droite])

    def crochet_infixe(self, gauche: Noeud) -> Noeud:
        """a[0], a[1:2], a[*]."""
        if self.genre() in ("number", "colon"):
            droite = self.index_ou_tranche()
            if gauche.genre == "index_expression":
                gauche.enfants.append(droite)
                return gauche
            return self.projeter_si_tranche(gauche, droite)
        self.exiger("star")
        self.exiger("rbracket")
        return Noeud("projection", [gauche, self.droite_projection(PUISSANCES["star"])])

    def fonction(self, gauche: Noeud, jeton: Jeton) -> Noeud:
        """Appel de fonction : seule length() est connue du moteur stdlib."""
        if gauche.genre != "field":
            raise ErreurRequete(f"syntaxe : nom de fonction invalide en position {jeton.position}")
        nom = gauche.valeur
        if nom not in FONCTIONS_JMESPATH:
            raise ErreurRequete(f"fonction inconnue de JMESPath : {nom}()")
        if nom != "length":
            raise self.refuser(f"fonction {nom}()", jeton)
        arguments = []
        while self.genre() != "rparen":
            argument = self.expression(0)
            if self.genre() == "comma":
                self.exiger("comma")
            arguments.append(argument)
        self.exiger("rparen")
        return Noeud("function_expression", arguments, nom)

    def filtre(self, gauche: Noeud) -> Noeud:
        """[?condition] puis la suite de la projection."""
        condition = self.expression(0)
        self.exiger("rbracket")
        droite = Noeud("identity") if self.genre() == "flatten" else self.droite_projection(PUISSANCES["filter"])
        return Noeud("filter_projection", [gauche, droite, condition])

    def index_ou_tranche(self) -> Noeud:
        """[n] ou [début:fin:pas]."""
        if self.genre() == "colon" or self.genre(1) == "colon":
            return self.tranche()
        valeur = self.avancer().valeur
        self.exiger("rbracket")
        return Noeud("index", valeur=valeur)

    def tranche(self) -> Noeud:
        """[début:fin:pas], chaque partie facultative."""
        parties: list[int | None] = [None, None, None]
        rang = 0
        while self.genre() != "rbracket" and rang < 3:
            if self.genre() == "colon":
                rang += 1
                if rang == 3:
                    raise ErreurRequete("syntaxe : trop de « : » dans une tranche")
                self.avancer()
            elif self.genre() == "number":
                parties[rang] = self.avancer().valeur
            else:
                jeton = self.jetons[self.index]
                raise ErreurRequete(f"syntaxe : jeton {jeton.valeur!r} inattendu dans une tranche")
        self.exiger("rbracket")
        return Noeud("slice", valeur=tuple(parties))

    def projeter_si_tranche(self, gauche: Noeud, droite: Noeud) -> Noeud:
        """Une tranche ouvre une projection, un index non."""
        expression = Noeud("index_expression", [gauche, droite])
        if droite.genre == "slice":
            return Noeud("projection", [expression, self.droite_projection(PUISSANCES["star"])])
        return expression

    def droite_projection(self, puissance: int) -> Noeud:
        """Ce qui s'applique à chaque élément d'une projection."""
        genre = self.genre()
        if PUISSANCES[genre] < ARRET_PROJECTION:
            return Noeud("identity")
        if genre in ("lbracket", "filter"):
            return self.expression(puissance)
        if genre == "dot":
            self.exiger("dot")
            return self.droite_point(puissance)
        jeton = self.jetons[self.index]
        raise ErreurRequete(f"syntaxe : jeton {jeton.valeur!r} inattendu après une projection")

    def droite_point(self, puissance: int) -> Noeud:
        """Ce qui suit un point : identifiant ou * ; .[a] et .{a} refusés."""
        genre = self.genre()
        if genre in ("quoted_identifier", "unquoted_identifier", "star"):
            return self.expression(puissance)
        jeton = self.jetons[self.index]
        if genre == "lbracket":
            raise self.refuser("sélection multiple .[expr, expr]", jeton)
        if genre == "lbrace":
            raise self.refuser("sélection multiple .{clé: expr}", jeton)
        raise ErreurRequete(f"syntaxe : identifiant attendu après « . », trouvé {jeton.valeur!r}")


# --------------------------------------------------------------------------- #
# Évaluation (sémantique de la référence)

def est_nombre(valeur: Any) -> bool:
    """Nombre JSON (les booléens Python n'en sont pas)."""
    return isinstance(valeur, (int, float)) and not isinstance(valeur, bool)


def est_faux(valeur: Any) -> bool:
    """Fausseté JMESPath : null, false, "", [], {}."""
    return valeur is None or valeur is False or valeur == "" or valeur == [] or valeur == {}


def egaux(gauche: Any, droite: Any) -> bool:
    """Égalité de la référence : 0 et 1 ne valent jamais false et true."""
    if est_nombre(gauche) and gauche in (0, 1) and isinstance(droite, bool):
        return False
    if est_nombre(droite) and droite in (0, 1) and isinstance(gauche, bool):
        return False
    return bool(gauche == droite)


def comparer_ordre(operateur: str, gauche: Any, droite: Any) -> bool | None:
    """<, <=, >, >= sur deux nombres ou deux chaînes ; null sinon."""
    comparables = (est_nombre(gauche) and est_nombre(droite)) or (isinstance(gauche, str) and isinstance(droite, str))
    if not comparables:
        return None
    return {"lt": gauche < droite, "lte": gauche <= droite, "gt": gauche > droite, "gte": gauche >= droite}[operateur]


class Evaluateur:
    """Parcourt l'arbre ; chaque genre de nœud a sa méthode."""

    def evaluer(self, noeud: Noeud, valeur: Any) -> Any:
        """Valeur du nœud appliqué à `valeur`."""
        return getattr(self, f"_{noeud.genre}")(noeud, valeur)

    def _field(self, noeud: Noeud, valeur: Any) -> Any:
        return valeur.get(noeud.valeur) if isinstance(valeur, dict) else None

    def _subexpression(self, noeud: Noeud, valeur: Any) -> Any:
        for enfant in noeud.enfants:
            valeur = self.evaluer(enfant, valeur)
        return valeur

    _index_expression = _subexpression

    def _pipe(self, noeud: Noeud, valeur: Any) -> Any:
        return self.evaluer(noeud.enfants[1], self.evaluer(noeud.enfants[0], valeur))

    def _identity(self, noeud: Noeud, valeur: Any) -> Any:
        return valeur

    _current = _identity

    def _literal(self, noeud: Noeud, valeur: Any) -> Any:
        return noeud.valeur

    def _index(self, noeud: Noeud, valeur: Any) -> Any:
        if not isinstance(valeur, list) or not -len(valeur) <= noeud.valeur < len(valeur):
            return None
        return valeur[noeud.valeur]

    def _slice(self, noeud: Noeud, valeur: Any) -> Any:
        if not isinstance(valeur, list):
            return None
        if noeud.valeur[2] == 0:
            raise ErreurRequete("valeur : le pas d'une tranche ne peut pas être nul")
        return valeur[slice(*noeud.valeur)]

    def _projection(self, noeud: Noeud, valeur: Any) -> Any:
        base = self.evaluer(noeud.enfants[0], valeur)
        if not isinstance(base, list):
            return None
        return self.collecter(noeud.enfants[1], base)

    def _value_projection(self, noeud: Noeud, valeur: Any) -> Any:
        base = self.evaluer(noeud.enfants[0], valeur)
        if not isinstance(base, dict):
            return None
        return self.collecter(noeud.enfants[1], list(base.values()))

    def _filter_projection(self, noeud: Noeud, valeur: Any) -> Any:
        base = self.evaluer(noeud.enfants[0], valeur)
        if not isinstance(base, list):
            return None
        retenus = [e for e in base if not est_faux(self.evaluer(noeud.enfants[2], e))]
        return self.collecter(noeud.enfants[1], retenus)

    def collecter(self, droite: Noeud, elements: list[Any]) -> list[Any]:
        """Applique la droite d'une projection ; les null sont écartés."""
        resultats = (self.evaluer(droite, e) for e in elements)
        return [r for r in resultats if r is not None]

    def _flatten(self, noeud: Noeud, valeur: Any) -> Any:
        base = self.evaluer(noeud.enfants[0], valeur)
        if not isinstance(base, list):
            return None
        aplati: list[Any] = []
        for element in base:
            aplati.extend(element if isinstance(element, list) else [element])
        return aplati

    def _comparator(self, noeud: Noeud, valeur: Any) -> Any:
        gauche = self.evaluer(noeud.enfants[0], valeur)
        droite = self.evaluer(noeud.enfants[1], valeur)
        if noeud.valeur == "eq":
            return egaux(gauche, droite)
        if noeud.valeur == "ne":
            return not egaux(gauche, droite)
        return comparer_ordre(noeud.valeur, gauche, droite)

    def _or_expression(self, noeud: Noeud, valeur: Any) -> Any:
        gauche = self.evaluer(noeud.enfants[0], valeur)
        return self.evaluer(noeud.enfants[1], valeur) if est_faux(gauche) else gauche

    def _and_expression(self, noeud: Noeud, valeur: Any) -> Any:
        gauche = self.evaluer(noeud.enfants[0], valeur)
        return gauche if est_faux(gauche) else self.evaluer(noeud.enfants[1], valeur)

    def _not_expression(self, noeud: Noeud, valeur: Any) -> Any:
        resultat = self.evaluer(noeud.enfants[0], valeur)
        if est_nombre(resultat) and resultat == 0:
            return False
        return not resultat

    def _function_expression(self, noeud: Noeud, valeur: Any) -> Any:
        arguments = [self.evaluer(a, valeur) for a in noeud.enfants]
        if len(arguments) != 1:
            raise ErreurRequete(f"type : length() attend 1 argument, reçu {len(arguments)}")
        argument = arguments[0]
        if not isinstance(argument, (str, list, dict)):
            raise ErreurRequete(f"type : length() attend une chaîne, un tableau ou un objet, reçu {type_json(argument)}")
        return len(argument)


def type_json(valeur: Any) -> str:
    """Nom JSON du type d'une valeur."""
    if valeur is None:
        return "null"
    if isinstance(valeur, bool):
        return "boolean"
    if est_nombre(valeur):
        return "number"
    return {str: "string", list: "array", dict: "object"}.get(type(valeur), type(valeur).__name__)


def evaluer_stdlib(expression: str, documents: list[Any]) -> list[Any]:
    """Moteur stdlib : une analyse, une évaluation par document."""
    arbre = Analyseur(expression).analyser()
    evaluateur = Evaluateur()
    try:
        return [evaluateur.evaluer(arbre, d) for d in documents]
    except RecursionError as exc:
        raise ErreurRequete("valeur : document trop profondément imbriqué pour l'évaluation") from exc


def evaluer_jmespath(expression: str, documents: list[Any]) -> list[Any]:
    """Moteur jmespath : erreurs de la bibliothèque traduites en code 2."""
    try:
        compilee = jmespath.compile(expression)
        return [compilee.search(d) for d in documents]
    except exceptions_jmespath.JMESPathError as exc:
        raise ErreurRequete(f"jmespath : {str(exc).splitlines()[0]}") from exc
    except (TypeError, ValueError) as exc:
        raise ErreurRequete(f"jmespath a levé {type(exc).__name__} : {exc}") from exc


# --------------------------------------------------------------------------- #
# Entrées

@dataclass
class Entree:
    """Documents à interroger et leur provenance."""

    documents: list[Any]
    noms: list[str]
    lignes: bool
    constantes: list[str]


def charger_json(texte: str, nom: str, constantes: list[str]) -> Any:
    """Un document JSON ; NaN/Infinity acceptés mais relevés."""
    def noter(constante: str) -> float:
        constantes.append(constante)
        return float(constante)

    try:
        return json.loads(texte, parse_constant=noter)
    except json.JSONDecodeError as exc:
        conseil = " (plusieurs documents : essayer --jsonl)" if exc.msg == "Extra data" else ""
        raise ErreurRequete(f"{nom} : JSON invalide ligne {exc.lineno} colonne {exc.colno} ({exc.msg}){conseil}") from exc
    except RecursionError as exc:
        raise ErreurRequete(f"{nom} : JSON trop profondément imbriqué") from exc


def charger_texte(texte: str, nom: str, lignes: bool) -> Entree:
    """Documents d'un texte JSON ou JSON Lines."""
    constantes: list[str] = []
    if not lignes:
        return Entree([charger_json(texte, nom, constantes)], [nom], False, constantes)
    documents, noms = [], []
    for numero, ligne in enumerate(texte.splitlines(), start=1):
        if ligne.strip():
            documents.append(charger_json(ligne, f"{nom} ligne {numero}", constantes))
            noms.append(f"{nom}:{numero}")
    return Entree(documents, noms, True, constantes)


def lire_fichier(chemin: Path, plafond: int, forcer_lignes: bool) -> Entree:
    """Lit un fichier JSON ou JSON Lines, borné en taille."""
    if not chemin.exists():
        raise ErreurRequete(f"chemin introuvable : {chemin}")
    if chemin.is_dir():
        raise ErreurRequete(f"{chemin} est un dossier : attendu un fichier JSON ou JSON Lines")
    taille = chemin.stat().st_size
    if taille > plafond:
        raise ErreurRequete(f"{chemin.name} pèse {taille} octets, au-delà de --max-octets {plafond}")
    try:
        texte = chemin.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurRequete(f"{chemin.name} n'est pas du texte utf-8 (octet {exc.start}) : pas du JSON") from exc
    lignes = forcer_lignes or chemin.suffix.lower() in EXTENSIONS_LIGNES
    return charger_texte(texte, chemin.name, lignes)


# --------------------------------------------------------------------------- #
# Exécution et sortie

def evaluer(expression: str, entree: Entree, moteur: str) -> dict[str, Any]:
    """Résultats selon le moteur choisi ; « comparer » confronte les deux."""
    if moteur == "comparer":
        return comparer_moteurs(expression, entree.documents)
    if moteur == "jmespath":
        return {"moteur": "jmespath", "resultats": evaluer_jmespath(expression, entree.documents)}
    return {"moteur": "stdlib", "resultats": evaluer_stdlib(expression, entree.documents)}


def comparer_moteurs(expression: str, documents: list[Any]) -> dict[str, Any]:
    """Évalue avec les deux moteurs ; une erreur d'un seul côté est un désaccord."""
    issues: dict[str, Any] = {}
    for nom, fonction in (("stdlib", evaluer_stdlib), ("jmespath", evaluer_jmespath)):
        try:
            issues[nom] = (fonction(expression, documents), None)
        except ErreurRequete as exc:
            issues[nom] = (None, str(exc))
    (stdlib, erreur_stdlib), (reference, erreur_reference) = issues["stdlib"], issues["jmespath"]
    if erreur_stdlib and erreur_reference:
        raise ErreurRequete(f"les deux moteurs échouent : {erreur_stdlib} | {erreur_reference}")
    accord = erreur_stdlib is None and erreur_reference is None and \
        json.dumps(stdlib, sort_keys=True) == json.dumps(reference, sort_keys=True)
    return {
        "moteur": "stdlib+jmespath",
        "resultats": reference if erreur_reference is None else stdlib,
        "accord_moteurs": accord,
        "resultats_stdlib": None if accord else stdlib,
        "erreurs_moteurs": {"stdlib": erreur_stdlib, "jmespath": erreur_reference},
    }


def assembler(expression: str, entree: Entree, evaluation: dict[str, Any]) -> dict[str, Any]:
    """Objet JSON final ; `denominateur` en tête (documents évalués)."""
    resultats = evaluation["resultats"]
    faux = sum(1 for r in resultats if est_faux(r))
    sortie: dict[str, Any] = {
        "denominateur": len(entree.documents),
        "examines": entree.noms[:LIMITE_EXAMINES],
        "examines_tronques": len(entree.noms) > LIMITE_EXAMINES,
        "moteur": evaluation["moteur"],
        "expression": expression,
        "json_lines": entree.lignes,
        "resultats_faux_ou_nuls": faux,
    }
    if entree.lignes:
        sortie["resultats"] = [{"document": n, "resultat": r} for n, r in zip(entree.noms, resultats)]
    else:
        sortie["resultat"] = resultats[0] if resultats else None
        sortie["type_resultat"] = type_json(sortie["resultat"])
    for cle in ("accord_moteurs", "resultats_stdlib", "erreurs_moteurs"):
        if cle in evaluation:
            sortie[cle] = evaluation[cle]
    sortie["contrat"] = extraire_contrat(__doc__ or "")
    return sortie


def extraire_contrat(doc: str) -> dict[str, str]:
    """Découpe la docstring en sections selon les intitulés du contrat."""
    contrat: dict[str, str] = {}
    courant: str | None = None
    for ligne in doc.splitlines():
        tete = ligne.strip()
        if tete in INTITULES:
            courant = tete
            contrat[courant] = ""
        elif courant is not None:
            contrat[courant] = (contrat[courant] + " " + tete).strip()
    return contrat


def afficher_json(sortie: dict[str, Any]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(sortie, ensure_ascii=False, indent=2))


def afficher_humain(sortie: dict[str, Any]) -> None:
    """Le résultat seul, comme jq : indenté, ou une ligne compacte par document."""
    if sortie["json_lines"]:
        for element in sortie["resultats"]:
            print(json.dumps(element["resultat"], ensure_ascii=False))
    else:
        print(json.dumps(sortie["resultat"], ensure_ascii=False, indent=2))


def choisir_moteur(demande: str) -> str:
    """Moteur effectif ; une ligne sur stderr si jmespath manque en mode auto."""
    if demande in ("jmespath", "comparer") and jmespath is None:
        raise ErreurRequete(f"--moteur {demande} demandé mais jmespath n'est pas installé")
    if demande == "auto":
        if jmespath is None:
            print("jmespath absent : moteur stdlib (sous-ensemble documenté, refus explicite du reste)",
                  file=sys.stderr)
            return "stdlib"
        return "jmespath"
    return demande


def construire_analyseur() -> argparse.ArgumentParser:
    """Analyseur d'arguments en français."""
    analyseur = argparse.ArgumentParser(
        description="Évalue une expression JMESPath sur un document JSON ou sur chaque ligne d'un "
                    "fichier JSON Lines. Code 1 si le résultat est faux au sens JMESPath (null, "
                    "false, chaîne, tableau ou objet vide) pour tous les documents.",
        epilog="Exemple : python3 interroger_json.py \"instances[?etat == 'actif'].id\" inventaire.json",
    )
    analyseur.add_argument("expression", help="expression JMESPath (nombres littéraux entre accents graves : `3`)")
    analyseur.add_argument("chemin", nargs="?", help="fichier .json, .jsonl ou .ndjson")
    analyseur.add_argument("--texte", help="document JSON passé directement (au lieu d'un chemin)")
    analyseur.add_argument("--jsonl", action="store_true", help="traiter l'entrée comme du JSON Lines")
    analyseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    analyseur.add_argument("--racine", help="dossier de base des chemins relatifs (défaut : dossier courant)")
    analyseur.add_argument("--moteur", choices=("auto", "stdlib", "jmespath", "comparer"), default="auto",
                           help="auto : jmespath s'il est installé, stdlib sinon ; comparer : les deux")
    analyseur.add_argument("--max-octets", type=int, default=256 * 1024 * 1024, help="taille maximale de l'entrée")
    return analyseur


def resoudre(chemin: str, racine: str | None) -> Path:
    """Chemin relatif résolu contre --racine (ou le dossier courant)."""
    brut = Path(chemin)
    if brut.is_absolute() or racine is None:
        return brut
    return Path(racine) / brut


def executer(args: argparse.Namespace) -> dict[str, Any]:
    """Charge l'entrée, évalue, assemble."""
    if (args.chemin is None) == (args.texte is None):
        raise ErreurRequete("donner soit un chemin, soit --texte (et pas les deux)")
    moteur = choisir_moteur(args.moteur)
    plafond = max(args.max_octets, 1024)
    if args.texte is not None:
        if len(args.texte.encode("utf-8")) > plafond:
            raise ErreurRequete(f"--texte dépasse {plafond} octets")
        entree = charger_texte(args.texte, "(--texte)", args.jsonl)
    else:
        entree = lire_fichier(resoudre(args.chemin, args.racine), plafond, args.jsonl)
    if entree.constantes:
        print(f"constantes non standard acceptées : {', '.join(sorted(set(entree.constantes)))}", file=sys.stderr)
    return assembler(args.expression, entree, evaluer(args.expression, entree, moteur))


def main() -> int:
    """Point d'entrée : 0 résultat vrai, 1 faux ou désaccord, 2 erreur ou refus, 3 aucun document."""
    args = construire_analyseur().parse_args()
    try:
        sortie = executer(args)
    except ErreurRequete as exc:
        print(f"interroger_json : {exc}", file=sys.stderr)
        if args.json:
            afficher_json({"denominateur": 0, "examines": [], "moteur": "stdlib", "erreur": str(exc), "code": exc.code})
        return exc.code
    if args.json:
        afficher_json(sortie)
    else:
        afficher_humain(sortie)
    if sortie["denominateur"] == 0:
        print("dénominateur nul : aucun document dans l'entrée, rien à examiner", file=sys.stderr)
        return 3
    if sortie.get("accord_moteurs") is False:
        print("désaccord entre les moteurs stdlib et jmespath (voir resultats_stdlib)", file=sys.stderr)
        return 1
    return 1 if sortie["resultats_faux_ou_nuls"] == sortie["denominateur"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
