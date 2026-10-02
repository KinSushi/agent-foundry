r"""Un agent qui relit une spécification OpenAPI « à l'œil » ne résout pas les
$ref ni ne croise les paramètres de chemin avec les gabarits. Mesuré : sur
la spécification Stripe v2535 (8,3 Mo, 7 297 $ref locales),
openapi-spec-validator 0.9.0 et cet outil jugent tous deux le document
valide, mais l'outil y relève en plus 2 schémas de composants orphelins ;
sur un document où /pets/{petId} ne déclare pas petId, les deux le jugent
invalide.

QUESTION
    Cette spécification OpenAPI 3.0 ou 3.1 est-elle valide et cohérente :
    champs obligatoires présents, références internes résolubles,
    paramètres de chemin déclarés et utilisés, réponses présentes et codes
    valides, composants réellement utilisés ?
MESURE
    Lecture JSON (stdlib, clés dupliquées détectées) ou YAML (PyYAML si
    présente, sinon lecteur YAML par blocs de la stdlib). Puis contrôles
    par règle, chacun localisé par un pointeur JSON : version et info,
    chemins (forme, gabarits équivalents), paramètres (name/in, schema ou
    content, chemin requis, doublons, gabarit <-> déclaration),
    operationId uniques (chemins, webhooks, callbacks), réponses (présence,
    codes 100 à 599, plages 1XX à 5XX ou default, description), corps de requête,
    serveurs et variables, exigences de sécurité vers des schémas
    déclarés, $ref locales résolues par pointeur JSON (refs externes
    listées, jamais suivies), composants orphelins par accessibilité depuis
    les chemins et webhooks, cohérence légère des schémas (type, items,
    required, nullable, exclusiveMinimum selon la version). Avec
    openapi-spec-validator et prance installées : même document validé par
    elles, accord ou désaccord rapporté.
HYPOTHÈSES
    Un document par fichier, encodé en UTF-8 ; les références externes
    sont hors du périmètre (listées, non suivies, aucun accès réseau ni
    fichier voisin) ; une erreur au sens de l'outil est une violation
    d'une exigence impérative de la spécification, un avertissement une
    incohérence probable (orphelin, corps sur GET, schéma de sécurité
    inutilisé).
LIMITES
    Pas de validation complète par méta-schéma : les mots-clés JSON Schema
    exotiques ne sont pas contrôlés ; les $ref vers un $id ou un $anchor
    hors du document ne sont pas suivies ; les exemples ne sont pas
    validés contre leur schéma. Le lecteur YAML stdlib couvre les blocs,
    séquences, scalaires simples, entre guillemets et littéraux (| et >),
    les collections en flux, ancres, alias et clés de fusion ; il refuse
    explicitement les étiquettes (!tag), les clés complexes (?) et les
    documents multiples, et suit le schéma YAML 1.2 (yes/no restent du
    texte, alors que PyYAML les lit comme booléens). OpenAPI 3.2 est
    contrôlée avec les règles 3.1 ; Swagger 2.0 est refusé.
CONTRE-EXEMPLES
    Un paramètre de chemin déclaré par un $ref vers un fichier externe
    n'est pas résolu : l'outil ne peut pas savoir qu'il couvre {id} et le
    signale comme non déclaré (constaté sur un essai avec
    $ref: params.yaml#/id). Les deux composants orphelins de Stripe ne sont
    peut-être pas morts : un client peut les viser par leur nom hors du
    document ; l'outil ne voit que les $ref.
INVOCATION
    {outil} --texte '{"openapi":"3.0.3","info":{"title":"t","version":"1"},"paths":{"/pets/{petId}":{"get":{"operationId":"lire","responses":{"200":{"description":"ok"}}}}}}' --json
DOMAINE
    Revue de contrat d'API avant publication, génération de client ou mise
    en production ; documents OpenAPI 3.0.x et 3.1.x autonomes.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import unquote

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import yaml as module_yaml
except ImportError:
    module_yaml = None

try:
    import openapi_spec_validator as module_osv
except ImportError:
    module_osv = None

try:
    import prance as module_prance
    from prance.util import resolver as module_prance_resolver
except ImportError:
    module_prance = None
    module_prance_resolver = None

RACINE = Path(__file__).resolve().parent

TITRE_QUESTION = "QUESTION"
TITRE_MESURE = "MESURE"
TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_LIMITES = "LIMITES"
TITRE_CONTRE_EXEMPLES = "CONTRE-EXEMPLES"
TITRE_INVOCATION = "INVOCATION"
TITRE_DOMAINE = "DOMAINE"
TITRES_CONTRAT = (TITRE_QUESTION, TITRE_MESURE, TITRE_HYPOTHESES, TITRE_LIMITES,
                  TITRE_CONTRE_EXEMPLES, TITRE_INVOCATION, TITRE_DOMAINE)
FORMAT_YAML = "YAML"
ENCODAGE_ATTENDU = "UTF-8"
GRAVITE_ERREUR = "erreur"
GRAVITE_AVERTISSEMENT = "avertissement"

EXTENSIONS = (".json", ".yaml", ".yml")
TAILLE_MAX = 64 * 1024 * 1024
LIMITE_FICHIERS = 5000
LIMITE_EXAMINES = 50
LIMITE_CONSTATS = 500
LIMITE_ERREURS_TIERCES = 20
LIMITE_CHAINE_REF = 64
METHODES = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
METHODES_SANS_CORPS = ("get", "head", "delete")
EMPLACEMENTS_PARAM = ("query", "header", "path", "cookie")
ENTETES_IGNOREES = ("accept", "content-type", "authorization")
TYPES_SCHEMA_30 = ("integer", "number", "string", "boolean", "array", "object")
TYPES_SCHEMA_31 = TYPES_SCHEMA_30 + ("null",)
TYPES_COMPOSANTS = ("schemas", "responses", "parameters", "examples", "requestBodies",
                    "headers", "links", "callbacks", "pathItems")
CLES_DONNEES = ("example", "default", "enum", "const")
CARTES_DE_NOMS = ("properties", "patternProperties", "$defs", "definitions", "schemas",
                  "responses", "parameters", "examples", "requestBodies", "headers",
                  "securitySchemes", "links", "callbacks", "pathItems", "paths", "webhooks",
                  "content", "encoding", "variables")
MOTS_SOUS_SCHEMAS = ("items", "not", "additionalProperties", "contains", "if", "then", "else",
                     "propertyNames", "unevaluatedItems", "unevaluatedProperties")
LISTES_SOUS_SCHEMAS = ("allOf", "anyOf", "oneOf", "prefixItems")
CARTES_SOUS_SCHEMAS = ("properties", "patternProperties", "$defs", "dependentSchemas")

MOTIF_VERSION = re.compile(r"^3\.([0-2])\.\d+(-[0-9A-Za-z.]+)?$")
MOTIF_VERSION_LACHE = re.compile(r"^3\.([0-2])\b")
MOTIF_CODE_HTTP = re.compile(r"^(default|[1-5][0-9]{2}|[1-5]XX)$")
MOTIF_GABARIT = re.compile(r"\{([^{}/]+)\}")
MOTIF_MEDIA = re.compile(r"^[^/\s;]+/[^/\s;]+(\s*;.*)?$")
MOTIF_NOM_COMPOSANT = re.compile(r"^[A-Za-z0-9._-]+$")

# ------------------------------------------------------------ lecteur YAML --

NULS_YAML = ("", "~", "null", "Null", "NULL")
VRAIS_YAML = ("true", "True", "TRUE")
FAUX_YAML = ("false", "False", "FALSE")
MOTIF_ENTIER = re.compile(r"^[-+]?[0-9]+$")
MOTIF_OCTAL = re.compile(r"^0o[0-7]+$")
MOTIF_HEXA = re.compile(r"^0x[0-9a-fA-F]+$")
MOTIF_REEL = re.compile(r"^[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?$")
MOTIF_INFINI = re.compile(r"^[-+]?\.(inf|Inf|INF)$")
MOTIF_NAN = re.compile(r"^\.(nan|NaN|NAN)$")
ECHAPPEMENTS_YAML = {"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n",
                     "v": "\v", "f": "\f", "r": "\r", "e": "\x1b", " ": " ", '"': '"',
                     "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0", "L": " ",
                     "P": " "}
LONGUEURS_ECHAPPEMENT = {"x": 2, "u": 4, "U": 8}


class ErreurEntree(Exception):
    """Entrée invalide : code 2."""


class ErreurYaml(ErreurEntree):
    """YAML illisible ou hors du sous-ensemble pris en charge."""

    def __init__(self, message: str, ligne: int) -> None:
        super().__init__(f"YAML ligne {ligne} : {message}")


@dataclass
class Ligne:
    """Ligne physique : numéro, retrait, texte sans retrait (brut : blancs finaux gardés)."""
    numero: int
    retrait: int
    texte: str
    brut: str = ""


def preparer_lignes(texte: str) -> list[Ligne]:
    """Découpe le texte en lignes ; refuse les tabulations de retrait."""
    lignes: list[Ligne] = []
    for numero, brute in enumerate(texte.splitlines(), start=1):
        contenu = brute.lstrip(" ")
        if contenu.startswith("\t") and contenu.strip():
            raise ErreurYaml("tabulation dans le retrait (interdite en YAML)", numero)
        lignes.append(Ligne(numero, len(brute) - len(contenu), contenu.rstrip(), contenu))
    return lignes


def resoudre_scalaire(texte: str) -> Any:
    """Type d'un scalaire simple selon le schéma YAML 1.2 (core)."""
    if texte in NULS_YAML:
        return None
    if texte in VRAIS_YAML:
        return True
    if texte in FAUX_YAML:
        return False
    if MOTIF_ENTIER.match(texte):
        return int(texte)
    if MOTIF_OCTAL.match(texte):
        return int(texte[2:], 8)
    if MOTIF_HEXA.match(texte):
        return int(texte[2:], 16)
    if MOTIF_REEL.match(texte):
        return float(texte)
    if MOTIF_INFINI.match(texte):
        return float("-inf") if texte.startswith("-") else float("inf")
    if MOTIF_NAN.match(texte):
        return float("nan")
    return texte


def sans_commentaire(texte: str) -> str:
    """Retire un commentaire « # » précédé d'un blanc (hors guillemets)."""
    if texte.startswith("#"):
        return ""
    guillemet = ""
    for i, car in enumerate(texte):
        if guillemet:
            if car == guillemet:
                guillemet = ""
        elif car in "\"'" and (i == 0 or texte[i - 1] in " \t[{,:"):
            guillemet = car
        elif car == "#" and texte[i - 1] in " \t":
            return texte[:i].rstrip()
    return texte


def fin_guillemets(texte: str, debut: int) -> int:
    """Indice du guillemet fermant (ou -1) ; '' et \\" sont des échappements."""
    quote = texte[debut]
    i = debut + 1
    while i < len(texte):
        car = texte[i]
        if quote == '"' and car == "\\":
            i += 2
            continue
        if car == quote:
            if quote == "'" and texte[i + 1:i + 2] == "'":
                i += 2
                continue
            return i
        i += 1
    return -1


def plier_lignes_guillemets(brut: str) -> str:
    """Pliage YAML d'un scalaire entre guillemets réparti sur plusieurs lignes."""
    morceaux = brut.split("\n")
    if len(morceaux) == 1:
        return brut
    resultat = morceaux[0].rstrip(" \t")
    vides = 0
    for morceau in morceaux[1:-1]:
        ligne = morceau.strip(" \t")
        if not ligne:
            vides += 1
            continue
        resultat = joindre_pli(resultat, ligne, vides)
        vides = 0
    return joindre_pli(resultat, morceaux[-1].lstrip(" \t"), vides)


def joindre_pli(resultat: str, suite: str, vides: int) -> str:
    """Saut de ligne plié : espace, ou « \\n » par ligne vide ; « \\ » final l'efface."""
    barres = len(resultat) - len(resultat.rstrip("\\"))
    if barres % 2 == 1:
        return resultat[:-1] + "\n" * vides + suite
    return resultat + ("\n" * vides if vides else " ") + suite


def desechapper_double(texte: str, numero: int) -> str:
    """Échappements d'un scalaire YAML entre guillemets doubles."""
    sortie: list[str] = []
    i = 0
    while i < len(texte):
        car = texte[i]
        if car != "\\":
            sortie.append(car)
            i += 1
            continue
        code = texte[i + 1:i + 2]
        if code in ECHAPPEMENTS_YAML:
            sortie.append(ECHAPPEMENTS_YAML[code])
            i += 2
        elif code in LONGUEURS_ECHAPPEMENT:
            longueur = LONGUEURS_ECHAPPEMENT[code]
            chiffres = texte[i + 2:i + 2 + longueur]
            try:
                sortie.append(chr(int(chiffres, 16)))
            except ValueError as exc:
                raise ErreurYaml(f"échappement \\{code}{chiffres} invalide", numero) from exc
            i += 2 + longueur
        else:
            raise ErreurYaml(f"échappement inconnu \\{code}", numero)
    return "".join(sortie)


def contenu_guillemets(brut: str, numero: int) -> str:
    """Contenu d'un scalaire entre guillemets (délimiteurs compris dans brut)."""
    interieur = plier_lignes_guillemets(brut[1:-1])
    if brut[0] == "'":
        return interieur.replace("''", "'")
    return desechapper_double(interieur, numero)


def separer_cle(texte: str, numero: int) -> tuple[str, str] | None:
    """(clé, reste) si la ligne commence par « clé: », sinon None."""
    if texte.startswith("? ") or texte == "?":
        raise ErreurYaml("clé complexe « ? » hors du sous-ensemble stdlib", numero)
    if not texte or texte[0] in "[{#|>%@`" or texte == "-" or texte.startswith("- "):
        return None
    if texte[0] in "\"'":
        fin = fin_guillemets(texte, 0)
        if fin < 0:
            return None
        apres = texte[fin + 1:].lstrip(" ")
        if apres.startswith(":") and apres[1:2] in ("", " ", "\t"):
            return contenu_guillemets(texte[:fin + 1], numero), apres[1:].strip()
        return None
    return separer_cle_simple(texte, numero)


def separer_cle_simple(texte: str, numero: int) -> tuple[str, str] | None:
    """Clé simple (non entre guillemets) suivie de « : »."""
    i = texte.find(":")
    while i != -1:
        if texte[i + 1:i + 2] in ("", " ", "\t"):
            avant = texte[:i]
            if " #" in avant or "\t#" in avant:
                return None
            if avant[:1] in ("&", "*", "!"):
                raise ErreurYaml("ancre, alias ou étiquette sur une clé : hors du sous-ensemble",
                                 numero)
            return avant.rstrip(), texte[i + 1:].strip()
        i = texte.find(":", i + 1)
    return None


def est_tiret(texte: str) -> bool:
    """Vrai si la ligne est une entrée de séquence en bloc."""
    return texte == "-" or texte.startswith("- ") or texte.startswith("-\t")


class LecteurFlux:
    """Collections en flux : [a, b], {c: d}, imbriquées."""

    def __init__(self, texte: str, numero: int, ancres: dict[str, Any]) -> None:
        self.texte = texte
        self.i = 0
        self.numero = numero
        self.ancres = ancres

    def erreur(self, message: str) -> ErreurYaml:
        """Erreur localisée à la ligne de début de la collection."""
        return ErreurYaml(f"{message} (collection en flux)", self.numero)

    def blancs(self) -> None:
        """Avance après les blancs."""
        while self.i < len(self.texte) and self.texte[self.i] in " \t\n":
            self.i += 1

    def valeur(self) -> Any:
        """Lit une valeur en flux."""
        self.blancs()
        if self.i >= len(self.texte):
            raise self.erreur("fin inattendue")
        car = self.texte[self.i]
        if car == "[":
            return self.sequence()
        if car == "{":
            return self.correspondance()
        if car in "\"'":
            return self.guillemets()
        if car == "*":
            return self.alias()
        if car in "&!":
            raise self.erreur("ancre ou étiquette en flux : hors du sous-ensemble")
        return resoudre_scalaire(self.simple())

    def guillemets(self) -> str:
        """Scalaire entre guillemets en flux."""
        fin = fin_guillemets(self.texte, self.i)
        if fin < 0:
            raise self.erreur("guillemet non fermé")
        brut = self.texte[self.i:fin + 1]
        self.i = fin + 1
        return contenu_guillemets(brut, self.numero)

    def alias(self) -> Any:
        """Alias *nom vers une ancre déjà définie."""
        fin = self.i + 1
        while fin < len(self.texte) and self.texte[fin] not in " \t\n,]}":
            fin += 1
        nom = self.texte[self.i + 1:fin]
        self.i = fin
        if nom not in self.ancres:
            raise self.erreur(f"alias *{nom} sans ancre")
        return self.ancres[nom]

    def simple(self) -> str:
        """Scalaire simple en flux, arrêté par , ] } ou « : »."""
        debut = self.i
        while self.i < len(self.texte):
            car = self.texte[self.i]
            if car in ",]}":
                break
            if car == ":" and self.texte[self.i + 1:self.i + 2] in ("", " ", "\t", "\n", ",", "]", "}"):
                break
            self.i += 1
        return self.texte[debut:self.i].strip()

    def sequence(self) -> list[Any]:
        """[a, b, ...]"""
        self.i += 1
        elements: list[Any] = []
        while True:
            self.blancs()
            if self.texte[self.i:self.i + 1] == "]":
                self.i += 1
                return elements
            elements.append(self.element_sequence())
            self.blancs()
            if self.texte[self.i:self.i + 1] == ",":
                self.i += 1
            elif self.texte[self.i:self.i + 1] != "]":
                raise self.erreur("« , » ou « ] » attendu")

    def element_sequence(self) -> Any:
        """Élément de séquence, éventuellement paire « clé: valeur » isolée."""
        element = self.valeur()
        self.blancs()
        if self.texte[self.i:self.i + 1] == ":":
            self.i += 1
            return {cle_texte(element): self.valeur()}
        return element

    def correspondance(self) -> dict[str, Any]:
        """{clé: valeur, ...}"""
        self.i += 1
        resultat: dict[str, Any] = {}
        while True:
            self.blancs()
            if self.texte[self.i:self.i + 1] == "}":
                self.i += 1
                return resultat
            cle = self.cle()
            resultat[cle] = self.valeur_apres_cle()
            self.blancs()
            if self.texte[self.i:self.i + 1] == ",":
                self.i += 1
            elif self.texte[self.i:self.i + 1] != "}":
                raise self.erreur("« , » ou « } » attendu")

    def cle(self) -> str:
        """Clé d'une correspondance en flux."""
        if self.texte[self.i:self.i + 1] in ("\"", "'"):
            return self.guillemets()
        return self.simple()

    def valeur_apres_cle(self) -> Any:
        """Valeur après « : » (absente = null)."""
        self.blancs()
        if self.texte[self.i:self.i + 1] != ":":
            return None
        self.i += 1
        self.blancs()
        if self.texte[self.i:self.i + 1] in (",", "}"):
            return None
        return self.valeur()


def cle_texte(valeur: Any) -> str:
    """Clé de correspondance ramenée au texte (comme en JSON)."""
    if isinstance(valeur, str):
        return valeur
    if valeur is None:
        return "null"
    if isinstance(valeur, bool):
        return "true" if valeur else "false"
    return str(valeur)


def equilibre_flux(texte: str) -> int:
    """Profondeur de crochets/accolades restant ouverte (hors guillemets)."""
    profondeur = 0
    i = 0
    while i < len(texte):
        car = texte[i]
        if car in "\"'" and (i == 0 or texte[i - 1] in " \t\n[{,:"):
            fin = fin_guillemets(texte, i)
            if fin < 0:
                return profondeur + 1
            i = fin
        elif car in "[{":
            profondeur += 1
        elif car in "]}":
            profondeur -= 1
        i += 1
    return profondeur


class LecteurYaml:
    """Lecteur YAML par blocs, sous-ensemble documenté dans LIMITES."""

    def __init__(self, texte: str) -> None:
        self.lignes = preparer_lignes(texte)
        self.pos = 0
        self.ancres: dict[str, Any] = {}
        self.doublons: list[str] = []

    def courante(self) -> Ligne:
        """Ligne au curseur."""
        return self.lignes[self.pos]

    def fini(self) -> bool:
        """Vrai en fin de document."""
        return self.pos >= len(self.lignes)

    def sauter_vides(self) -> None:
        """Avance après lignes vides et commentaires."""
        while not self.fini():
            texte = self.courante().texte
            if texte and not texte.startswith("#"):
                return
            self.pos += 1

    def lire(self) -> Any:
        """Lit le document entier."""
        self.preambule()
        self.sauter_vides()
        if self.fini():
            return None
        valeur = self.lire_bloc(self.courante().retrait)
        self.sauter_vides()
        if not self.fini():
            ligne = self.courante()
            if ligne.texte in ("---", "...") or ligne.texte.startswith("--- "):
                raise ErreurYaml("plusieurs documents : hors du sous-ensemble", ligne.numero)
            raise ErreurYaml("retrait incohérent", ligne.numero)
        return valeur

    def preambule(self) -> None:
        """Directives %… et marque de début « --- »."""
        self.sauter_vides()
        while not self.fini() and self.courante().texte.startswith("%"):
            self.pos += 1
            self.sauter_vides()
        if not self.fini() and self.courante().retrait == 0:
            texte = self.courante().texte
            if texte == "---" or texte.startswith("--- #"):
                self.pos += 1
            elif texte.startswith("--- "):
                raise ErreurYaml("contenu après « --- » : hors du sous-ensemble",
                                 self.courante().numero)

    def lire_bloc(self, retrait: int) -> Any:
        """Nœud en bloc commençant à la ligne courante."""
        ligne = self.courante()
        if est_tiret(ligne.texte):
            return self.lire_sequence(retrait)
        if separer_cle(ligne.texte, ligne.numero) is not None:
            return self.lire_correspondance(retrait)
        self.pos += 1
        return self.lire_valeur(ligne.texte, retrait - 1, ligne, False)

    def lire_correspondance(self, retrait: int) -> dict[str, Any]:
        """Correspondance en bloc au retrait donné."""
        resultat: dict[str, Any] = {}
        fusions: list[Any] = []
        while True:
            self.sauter_vides()
            if self.fini() or self.courante().retrait < retrait:
                break
            ligne = self.courante()
            if ligne.retrait > retrait or est_tiret(ligne.texte) or ligne.texte in ("---", "..."):
                break
            paire = separer_cle(ligne.texte, ligne.numero)
            if paire is None:
                raise ErreurYaml("clé « nom: » attendue", ligne.numero)
            self.pos += 1
            valeur = self.lire_valeur(paire[1], retrait, ligne, True)
            self.ranger(resultat, fusions, paire[0], valeur, ligne.numero)
        return appliquer_fusions(resultat, fusions)

    def ranger(self, resultat: dict[str, Any], fusions: list[Any], cle: str, valeur: Any,
               numero: int) -> None:
        """Range une paire ; « << » est une clé de fusion ; doublons notés."""
        if cle == "<<":
            fusions.extend(valeur if isinstance(valeur, list) else [valeur])
            return
        if cle in resultat:
            self.doublons.append(f"ligne {numero} : clé « {cle} » répétée")
        resultat[cle] = valeur

    def lire_sequence(self, retrait: int) -> list[Any]:
        """Séquence en bloc (« - ») au retrait donné."""
        elements: list[Any] = []
        while True:
            self.sauter_vides()
            if self.fini():
                break
            ligne = self.courante()
            if ligne.retrait != retrait or not est_tiret(ligne.texte):
                break
            elements.append(self.lire_element(ligne, retrait))
        return elements

    def lire_element(self, ligne: Ligne, retrait: int) -> Any:
        """Un élément « - … » : valeur en ligne, bloc compact ou bloc suivant."""
        reste = ligne.texte[1:]
        contenu = reste.lstrip(" \t")
        colonne = retrait + 1 + len(reste) - len(contenu)
        if est_tiret(contenu) or separer_cle(contenu, ligne.numero) is not None:
            self.lignes[self.pos] = Ligne(ligne.numero, colonne, contenu, contenu)
            return self.lire_bloc(colonne)
        self.pos += 1
        return self.lire_valeur(contenu, retrait, ligne, False)

    def lire_valeur(self, reste: str, parent: int, ligne: Ligne, en_correspondance: bool) -> Any:
        """Valeur après « clé: » ou « - » (même ligne ou lignes suivantes)."""
        ancre = ""
        if reste.startswith("&"):
            ancre, _, reste = reste[1:].partition(" ")
            reste = reste.strip()
        if reste.startswith("!"):
            raise ErreurYaml(f"étiquette « {reste.split()[0]} » hors du sous-ensemble stdlib "
                             "(installer PyYAML)", ligne.numero)
        if reste[:1] not in ("\"", "'"):
            reste = sans_commentaire(reste)
        valeur = self.valeur_brute(reste, parent, ligne, en_correspondance)
        if ancre:
            self.ancres[ancre] = valeur
        return valeur

    def valeur_brute(self, reste: str, parent: int, ligne: Ligne, en_correspondance: bool) -> Any:
        """Aiguillage selon le premier caractère de la valeur."""
        if not reste:
            return self.lire_valeur_suivante(parent, en_correspondance)
        if reste.startswith("*"):
            nom = reste[1:].strip()
            if nom not in self.ancres:
                raise ErreurYaml(f"alias *{nom} sans ancre", ligne.numero)
            return self.ancres[nom]
        if reste[0] in "|>":
            return self.lire_litteral(reste, parent, ligne.numero)
        if reste[0] in "[{":
            return self.lire_flux(reste, ligne.numero)
        if reste[0] in "\"'":
            return self.lire_guillemets(reste, ligne.numero)
        return self.lire_simple(reste, parent)

    def lire_valeur_suivante(self, parent: int, en_correspondance: bool) -> Any:
        """Valeur portée par les lignes suivantes (ou null)."""
        self.sauter_vides()
        if self.fini():
            return None
        ligne = self.courante()
        if ligne.retrait > parent:
            return self.lire_bloc(ligne.retrait)
        if en_correspondance and ligne.retrait == parent and est_tiret(ligne.texte):
            return self.lire_sequence(parent)
        return None

    def lire_simple(self, premier: str, parent: int) -> Any:
        """Scalaire simple, éventuellement continué sur des lignes plus retirées."""
        morceaux = [premier]
        vides = 0
        while not self.fini():
            ligne = self.courante()
            if not ligne.texte:
                vides += 1
                self.pos += 1
                continue
            if ligne.retrait <= parent or ligne.texte.startswith("#"):
                break
            morceaux.append(("\n" * vides if vides else " ") + sans_commentaire(ligne.texte))
            vides = 0
            self.pos += 1
        if vides:
            self.pos -= vides
        if len(morceaux) == 1:
            return resoudre_scalaire(premier)
        return "".join(morceaux)

    def lire_guillemets(self, premier: str, numero: int) -> str:
        """Scalaire entre guillemets, éventuellement sur plusieurs lignes."""
        brut = premier
        while fin_guillemets(brut, 0) < 0:
            if self.fini():
                raise ErreurYaml("guillemet non fermé", numero)
            brut += "\n" + self.courante().texte
            self.pos += 1
        fin = fin_guillemets(brut, 0)
        if sans_commentaire(brut[fin + 1:].strip()):
            raise ErreurYaml("texte après un scalaire entre guillemets", numero)
        return contenu_guillemets(brut[:fin + 1], numero)

    def lire_flux(self, premier: str, numero: int) -> Any:
        """Collection en flux, éventuellement sur plusieurs lignes."""
        brut = premier
        while equilibre_flux(brut) > 0:
            if self.fini():
                raise ErreurYaml("collection en flux non fermée", numero)
            brut += "\n" + sans_commentaire(self.courante().texte)
            self.pos += 1
        lecteur = LecteurFlux(brut, numero, self.ancres)
        valeur = lecteur.valeur()
        if sans_commentaire(brut[lecteur.i:].strip()):
            raise ErreurYaml("texte après une collection en flux", numero)
        return valeur

    def lire_litteral(self, entete: str, parent: int, numero: int) -> str:
        """Scalaire littéral « | » ou plié « > », avec indicateurs."""
        style, coupe, explicite = analyser_entete_litteral(entete, numero)
        brutes: list[Ligne] = []
        while not self.fini():
            ligne = self.courante()
            if ligne.texte and ligne.retrait <= parent:
                break
            brutes.append(ligne)
            self.pos += 1
        return assembler_litteral(brutes, style, coupe, parent + explicite if explicite else 0)


def analyser_entete_litteral(entete: str, numero: int) -> tuple[str, str, int]:
    """(style, coupe, retrait explicite) depuis « |-2 », « >+ », etc."""
    indicateurs = sans_commentaire(entete[1:]).strip()
    coupe, explicite = "clip", 0
    for car in indicateurs:
        if car == "-":
            coupe = "strip"
        elif car == "+":
            coupe = "keep"
        elif car.isdigit() and car != "0":
            explicite = int(car)
        else:
            raise ErreurYaml(f"indicateur de scalaire littéral inconnu « {car} »", numero)
    return entete[0], coupe, explicite


def assembler_litteral(brutes: list[Ligne], style: str, coupe: str, retrait: int) -> str:
    """Texte d'un scalaire littéral/plié selon la coupe (clip, strip, keep)."""
    fin = len(brutes)
    while fin and not brutes[fin - 1].texte:
        fin -= 1
    contenu = brutes[:fin]
    finales = len(brutes) - fin
    if not retrait:
        retrait = min((l.retrait for l in contenu if l.texte), default=0)
    lignes = [(" " * max(l.retrait - retrait, 0) + l.brut) if l.texte else "" for l in contenu]
    texte = "\n".join(lignes) if style == "|" else plier_bloc(lignes)
    if not texte:
        return "\n" * finales if coupe == "keep" else ""
    if coupe == "strip":
        return texte
    if coupe == "keep":
        return texte + "\n" * (1 + finales)
    return texte + "\n"


def plier_bloc(lignes: list[str]) -> str:
    """Pliage d'un scalaire « > » : sauts simples -> espace."""
    if not lignes:
        return ""
    morceaux = [lignes[0]]
    for precedente, ligne in zip(lignes, lignes[1:]):
        if not ligne:
            morceaux.append("\n")
        elif not precedente:
            morceaux.append(ligne)
        elif ligne.startswith(" ") or precedente.startswith(" "):
            morceaux.append("\n" + ligne)
        else:
            morceaux.append(" " + ligne)
    return "".join(morceaux)


def appliquer_fusions(resultat: dict[str, Any], fusions: list[Any]) -> dict[str, Any]:
    """Clés de fusion « << » : les clés explicites l'emportent."""
    for source in fusions:
        if isinstance(source, dict):
            for cle, valeur in source.items():
                resultat.setdefault(cle, valeur)
    return resultat


# --------------------------------------------------------------- lecture --

@dataclass
class Document:
    """Document chargé : source, contenu, moteur, clés dupliquées."""
    source: str
    contenu: Any
    moteur: str
    doublons: list[str] = field(default_factory=list)


def charger_json(texte: str, source: str) -> Document:
    """JSON stdlib avec détection des clés dupliquées."""
    doublons: list[str] = []

    def paires(liste: list[tuple[str, Any]]) -> dict[str, Any]:
        resultat: dict[str, Any] = {}
        for cle, valeur in liste:
            if cle in resultat:
                doublons.append(f"clé « {cle} » répétée")
            resultat[cle] = valeur
        return resultat

    try:
        contenu = json.loads(texte, object_pairs_hook=paires)
    except json.JSONDecodeError as exc:
        raise ErreurEntree(f"{source} : JSON invalide ligne {exc.lineno} colonne {exc.colno} "
                           f"({exc.msg})") from exc
    return Document(source, contenu, "stdlib", doublons)


def normaliser_tiers(valeur: Any) -> Any:
    """Ramène la sortie de PyYAML au modèle JSON (clés texte, dates en texte)."""
    if isinstance(valeur, dict):
        return {cle_texte(c): normaliser_tiers(v) for c, v in valeur.items()}
    if isinstance(valeur, list):
        return [normaliser_tiers(v) for v in valeur]
    if valeur is None or isinstance(valeur, (str, int, float, bool)):
        return valeur
    return str(valeur)


def charger_yaml(texte: str, source: str, force_stdlib: bool) -> Document:
    """YAML par PyYAML si présente (et non écartée), sinon lecteur stdlib."""
    if module_yaml is not None and not force_stdlib:
        chargeur = getattr(module_yaml, "CSafeLoader", module_yaml.SafeLoader)
        try:
            contenu = module_yaml.load(texte, Loader=chargeur)
        except module_yaml.YAMLError as exc:
            raise ErreurEntree(f"{source} : YAML invalide ({exc})") from exc
        except RecursionError as exc:
            raise ErreurEntree(f"{source} : YAML trop profond ou récursif") from exc
        return Document(source, normaliser_tiers(contenu), "pyyaml")
    lecteur = LecteurYaml(texte)
    try:
        contenu = lecteur.lire()
    except ErreurYaml as exc:
        raise ErreurEntree(f"{source} : {exc}") from exc
    return Document(source, contenu, "stdlib", lecteur.doublons)


def decoder(octets: bytes, source: str) -> str:
    """UTF-8 strict (BOM toléré) ; refuse le binaire."""
    if b"\x00" in octets[:8192]:
        raise ErreurEntree(f"{source} : contenu binaire (octet nul), pas une spécification")
    try:
        return octets.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{source} : pas de l'{ENCODAGE_ATTENDU} valide (octet {exc.start})") from exc


def charger_texte(texte: str, source: str, force_stdlib: bool) -> Document:
    """JSON si le texte commence par { ou [, sinon YAML."""
    debut = texte.lstrip()[:1]
    if debut in ("{", "["):
        return charger_json(texte, source)
    return charger_yaml(texte, source, force_stdlib)


def lire_fichier(chemin: Path) -> bytes:
    """Lecture bornée d'un fichier."""
    try:
        taille = chemin.stat().st_size
        if taille > TAILLE_MAX:
            raise ErreurEntree(f"{chemin} : {taille} octets, au-delà de la borne {TAILLE_MAX}")
        return chemin.read_bytes()
    except OSError as exc:
        raise ErreurEntree(f"{chemin} : illisible ({exc.strerror or exc})") from exc


def lister_fichiers(chemins: list[str], racine: Path | None) -> list[tuple[Path, bool]]:
    """(fichier, explicite) : fichiers donnés, ou .json/.yaml/.yml des dossiers."""
    trouves: list[tuple[Path, bool]] = []
    for brut in chemins:
        chemin = Path(brut)
        if racine is not None and not chemin.is_absolute():
            chemin = racine / chemin
        if chemin.is_dir():
            trouves.extend((p, False) for p in sorted(chemin.rglob("*"))
                           if p.is_file() and p.suffix.lower() in EXTENSIONS)
        elif chemin.is_file():
            trouves.append((chemin, True))
        else:
            raise ErreurEntree(f"{brut} : chemin inexistant")
        if len(trouves) > LIMITE_FICHIERS:
            raise ErreurEntree(f"plus de {LIMITE_FICHIERS} fichiers : restreindre la cible")
    return trouves


# -------------------------------------------------------- pointeurs JSON --

def pointeur(*parties: Any) -> str:
    """Pointeur JSON « #/a/b~1c » à partir de segments."""
    return "#/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parties)


def resoudre_pointeur(racine: Any, fragment: str) -> tuple[bool, Any]:
    """Résout « /a/b » (fragment déjà sans « # ») dans le document."""
    if fragment == "":
        return True, racine
    courant = racine
    for brut in fragment[1:].split("/"):
        segment = unquote(brut).replace("~1", "/").replace("~0", "~")
        if isinstance(courant, dict) and segment in courant:
            courant = courant[segment]
        elif isinstance(courant, list) and segment.isdigit() and int(segment) < len(courant):
            courant = courant[int(segment)]
        else:
            return False, None
    return True, courant


def est_ref_locale(ref: str) -> bool:
    """Référence interne au document (« #… »)."""
    return ref.startswith("#")


def suivre_ref(racine: Any, noeud: Any) -> Any:
    """Suit une chaîne de $ref locales ; None si irrésoluble, externe ou en boucle."""
    vus = 0
    while isinstance(noeud, dict) and isinstance(noeud.get("$ref"), str):
        ref = noeud["$ref"]
        vus += 1
        if not ref.startswith("#/") or vus > LIMITE_CHAINE_REF:
            return None
        trouve, noeud = resoudre_pointeur(racine, ref[1:])
        if not trouve:
            return None
    return noeud


# ------------------------------------------------------------- constats --

@dataclass
class Constats:
    """Accumulateur de constats d'un document."""
    liste: list[dict[str, str]] = field(default_factory=list)

    def ajouter(self, gravite: str, regle: str, emplacement: str, message: str) -> None:
        """Ajoute un constat."""
        self.liste.append({"gravite": gravite, "regle": regle, "emplacement": emplacement,
                           "message": message})

    def erreur(self, regle: str, emplacement: str, message: str) -> None:
        """Violation d'une exigence de la spécification."""
        self.ajouter(GRAVITE_ERREUR, regle, emplacement, message)

    def avertir(self, regle: str, emplacement: str, message: str) -> None:
        """Incohérence probable."""
        self.ajouter(GRAVITE_AVERTISSEMENT, regle, emplacement, message)

    def compter(self, gravite: str) -> int:
        """Nombre de constats d'une gravité."""
        return sum(1 for c in self.liste if c["gravite"] == gravite)


@dataclass
class Contexte:
    """Document en cours de contrôle."""
    racine: dict[str, Any]
    mineure: int
    constats: Constats
    operations: int = 0
    refs_locales: int = 0
    refs_externes: list[str] = field(default_factory=list)


# ------------------------------------------------------- contrôles racine --

def controler_racine(ctx: Contexte) -> None:
    """Champs obligatoires de premier niveau."""
    version = ctx.racine.get("openapi")
    if not isinstance(version, str) or not MOTIF_VERSION.match(version):
        ctx.constats.erreur("version", pointeur("openapi"), f"openapi doit être un texte de forme "
                            f"3.x.y (lu : {version!r})")
    if ctx.mineure == 2:
        ctx.constats.avertir("version", pointeur("openapi"), "OpenAPI 3.2 contrôlée avec les règles 3.1")
    info = ctx.racine.get("info")
    if not isinstance(info, dict):
        ctx.constats.erreur("info", pointeur("info"), "objet info absent ou invalide")
    else:
        for champ in ("title", "version"):
            if not isinstance(info.get(champ), str):
                ctx.constats.erreur("info", pointeur("info", champ),
                                    f"info.{champ} obligatoire (texte)")
    if ctx.mineure == 0 and not isinstance(ctx.racine.get("paths"), dict):
        ctx.constats.erreur("racine", pointeur("paths"), "paths obligatoire en OpenAPI 3.0")
    if ctx.mineure >= 1 and not any(k in ctx.racine for k in ("paths", "components", "webhooks")):
        ctx.constats.erreur("racine", "#", "au moins un de paths, components, webhooks est requis")


def controler_serveurs(ctx: Contexte, serveurs: Any, ou: str) -> None:
    """Server Object : url obligatoire, variables du gabarit déclarées."""
    if not isinstance(serveurs, list):
        return
    for index, serveur in enumerate(serveurs):
        emplacement = f"{ou}/servers/{index}"
        if not isinstance(serveur, dict) or not isinstance(serveur.get("url"), str):
            ctx.constats.erreur("serveur", emplacement, "serveur sans url")
            continue
        variables = serveur.get("variables") or {}
        for nom in MOTIF_GABARIT.findall(serveur["url"]):
            if nom not in variables:
                ctx.constats.erreur("serveur", emplacement,
                                    f"variable {{{nom}}} de l'url non déclarée dans variables")
        for nom, variable in variables.items() if isinstance(variables, dict) else ():
            controler_variable_serveur(ctx, variable, f"{emplacement}/variables/{nom}")


def controler_variable_serveur(ctx: Contexte, variable: Any, emplacement: str) -> None:
    """Variable de serveur : default obligatoire, présent dans enum."""
    if not isinstance(variable, dict) or "default" not in variable:
        ctx.constats.erreur("serveur", emplacement, "variable de serveur sans default")
        return
    enum = variable.get("enum")
    if isinstance(enum, list) and variable["default"] not in enum:
        ctx.constats.erreur("serveur", emplacement, "default absent de enum")


# ------------------------------------------------------ chemins, opérations --

def iterer_operations(ctx: Contexte) -> Iterator[tuple[str, str, str, dict[str, Any], dict[str, Any]]]:
    """(zone, chemin, méthode, opération, élément de chemin) : paths, webhooks, callbacks."""
    for zone in ("paths", "webhooks"):
        elements = ctx.racine.get(zone)
        if isinstance(elements, dict):
            for nom, element in elements.items():
                yield from operations_de(ctx, zone, nom, element, 0)


def operations_de(ctx: Contexte, zone: str, nom: str, element: Any,
                  profondeur: int) -> Iterator[tuple[str, str, str, dict[str, Any], dict[str, Any]]]:
    """Opérations d'un élément de chemin, puis de ses callbacks."""
    element = suivre_ref(ctx.racine, element)
    if not isinstance(element, dict) or profondeur > 3:
        return
    for methode in METHODES:
        operation = element.get(methode)
        if isinstance(operation, dict):
            yield zone, nom, methode, operation, element
            yield from operations_callbacks(ctx, operation, profondeur)


def operations_callbacks(ctx: Contexte, operation: dict[str, Any],
                         profondeur: int) -> Iterator[tuple[str, str, str, dict[str, Any], dict[str, Any]]]:
    """Opérations décrites dans les callbacks d'une opération."""
    rappels = operation.get("callbacks")
    if not isinstance(rappels, dict):
        return
    for nom, rappel in rappels.items():
        rappel = suivre_ref(ctx.racine, rappel)
        if isinstance(rappel, dict):
            for expression, element in rappel.items():
                yield from operations_de(ctx, "callbacks", f"{nom} {expression}", element,
                                         profondeur + 1)


def controler_chemins(ctx: Contexte) -> None:
    """Forme des clés de paths et gabarits équivalents."""
    chemins = ctx.racine.get("paths")
    if not isinstance(chemins, dict):
        return
    formes: dict[str, list[str]] = {}
    for chemin in chemins:
        if not chemin.startswith("/"):
            ctx.constats.erreur("chemin", pointeur("paths", chemin), "un chemin doit commencer par /")
        if "?" in chemin or "#" in chemin:
            ctx.constats.erreur("chemin", pointeur("paths", chemin),
                                "chaîne de requête ou fragment interdit dans un chemin")
        formes.setdefault(MOTIF_GABARIT.sub("{}", chemin), []).append(chemin)
    for groupe in formes.values():
        if len(groupe) > 1:
            ctx.constats.erreur("chemin-equivalent", pointeur("paths", groupe[1]),
                                f"gabarits équivalents (seuls les noms diffèrent) : {', '.join(groupe)}")


def emplacement_operation(zone: str, chemin: str, methode: str) -> str:
    """Pointeur d'une opération (callbacks : forme lisible)."""
    if zone == "callbacks":
        return f"callback {chemin} {methode}"
    return pointeur(zone, chemin, methode)


def controler_operations(ctx: Contexte) -> None:
    """Tous les contrôles par opération, puis operationId uniques."""
    identifiants: dict[str, list[str]] = {}
    for zone, chemin, methode, operation, element in iterer_operations(ctx):
        ctx.operations += 1
        ou = emplacement_operation(zone, chemin, methode)
        if isinstance(operation.get("operationId"), str):
            identifiants.setdefault(operation["operationId"], []).append(ou)
        controler_parametres(ctx, zone, chemin, operation, element, ou)
        controler_reponses(ctx, operation, ou)
        controler_corps(ctx, methode, operation, ou)
        controler_serveurs(ctx, operation.get("servers"), ou)
    for identifiant, lieux in identifiants.items():
        if len(lieux) > 1:
            ctx.constats.erreur("operationId", lieux[1],
                                f"operationId « {identifiant} » utilisé {len(lieux)} fois : "
                                + ", ".join(lieux[:5]))


# ------------------------------------------------------------ paramètres --

def parametres_resolus(ctx: Contexte, liste: Any, ou: str) -> tuple[dict[tuple[str, str], dict[str, Any]], int]:
    """{(in, nom) : paramètre} d'une liste, et nombre de paramètres non résolus."""
    resultat: dict[tuple[str, str], dict[str, Any]] = {}
    non_resolus = 0
    if not isinstance(liste, list):
        return resultat, 0
    for index, brut in enumerate(liste):
        parametre = suivre_ref(ctx.racine, brut)
        if not isinstance(parametre, dict):
            non_resolus += 1
            continue
        cle = cle_parametre(ctx, parametre, f"{ou}/parameters/{index}")
        if cle is None:
            continue
        if cle in resultat:
            ctx.constats.erreur("parametre", f"{ou}/parameters/{index}",
                                f"paramètre {cle[1]} ({cle[0]}) déclaré deux fois au même niveau")
        resultat[cle] = parametre
    return resultat, non_resolus


def cle_parametre(ctx: Contexte, parametre: dict[str, Any], ou: str) -> tuple[str, str] | None:
    """Contrôle name/in/schema d'un paramètre ; rend (in, nom normalisé)."""
    nom, lieu = parametre.get("name"), parametre.get("in")
    if not isinstance(nom, str) or lieu not in EMPLACEMENTS_PARAM:
        ctx.constats.erreur("parametre", ou, "paramètre sans name ou avec un in invalide "
                            f"(in={lieu!r}, attendu : {', '.join(EMPLACEMENTS_PARAM)})")
        return None
    if ("schema" in parametre) == ("content" in parametre):
        ctx.constats.erreur("parametre", ou, f"paramètre {nom} : exactement un de schema ou content")
    if lieu == "header" and nom.lower() in ENTETES_IGNOREES:
        ctx.constats.avertir("parametre", ou, f"en-tête {nom} décrit en paramètre : ignoré par la "
                             "spécification (utiliser content ou securitySchemes)")
    return lieu, nom.lower() if lieu == "header" else nom


def controler_parametres(ctx: Contexte, zone: str, chemin: str, operation: dict[str, Any],
                         element: dict[str, Any], ou: str) -> None:
    """Paramètres de l'opération fusionnés avec ceux du chemin ; gabarit <-> path."""
    communs, n1 = parametres_resolus(ctx, element.get("parameters"), ou.rsplit("/", 1)[0])
    propres, n2 = parametres_resolus(ctx, operation.get("parameters"), ou)
    effectifs = {**communs, **propres}
    declares = {nom for (lieu, nom) in effectifs if lieu == "path"}
    for (lieu, nom), parametre in effectifs.items():
        if lieu == "path" and parametre.get("required") is not True:
            ctx.constats.erreur("parametre-chemin", ou, f"paramètre de chemin {nom} sans required: true")
    if zone != "paths":
        return
    gabarit = set(MOTIF_GABARIT.findall(chemin))
    for nom in sorted(gabarit - declares):
        complement = " (des paramètres $ref n'ont pas pu être résolus)" if n1 + n2 else ""
        ctx.constats.erreur("parametre-chemin", ou, f"{{{nom}}} figure dans le chemin mais aucun "
                            f"paramètre in: path ne le déclare{complement}")
    for nom in sorted(declares - gabarit):
        ctx.constats.erreur("parametre-chemin", ou,
                            f"paramètre in: path « {nom} » absent du gabarit {chemin}")


# -------------------------------------------------------------- réponses --

def controler_reponses(ctx: Contexte, operation: dict[str, Any], ou: str) -> None:
    """Présence des réponses, codes valides, description."""
    reponses = operation.get("responses")
    if not isinstance(reponses, dict) or not reponses:
        message = "aucune réponse décrite"
        if ctx.mineure == 0:
            ctx.constats.erreur("reponses", ou, message + " (responses obligatoire en 3.0)")
        else:
            ctx.constats.avertir("reponses", ou, message)
        return
    for code, brute in reponses.items():
        lieu = f"{ou}/responses/{code}"
        if code.startswith("x-"):
            continue
        if not MOTIF_CODE_HTTP.match(code):
            ctx.constats.erreur("code-http", lieu, f"code de réponse « {code} » invalide "
                                "(100-599, 1XX-5XX en majuscules, ou default)")
        reponse = suivre_ref(ctx.racine, brute)
        if isinstance(brute, dict) and "$ref" not in brute and not isinstance(brute.get("description"), str):
            ctx.constats.erreur("reponse-description", lieu, "réponse sans description")
        if isinstance(reponse, dict):
            controler_contenu(ctx, reponse.get("content"), lieu)


def controler_corps(ctx: Contexte, methode: str, operation: dict[str, Any], ou: str) -> None:
    """Corps de requête : content obligatoire ; corps sur GET/HEAD/DELETE signalé."""
    if "requestBody" not in operation:
        return
    corps = suivre_ref(ctx.racine, operation["requestBody"])
    if isinstance(corps, dict):
        if not isinstance(corps.get("content"), dict):
            ctx.constats.erreur("corps", f"{ou}/requestBody", "requestBody sans content")
        controler_contenu(ctx, corps.get("content"), f"{ou}/requestBody")
    if methode in METHODES_SANS_CORPS:
        ctx.constats.avertir("corps", f"{ou}/requestBody", f"corps de requête sur {methode.upper()} : "
                             "sémantique non définie par HTTP, souvent ignoré par les clients")


def controler_contenu(ctx: Contexte, contenu: Any, ou: str) -> None:
    """Clés de content : types de média."""
    if not isinstance(contenu, dict):
        return
    for media in contenu:
        if not MOTIF_MEDIA.match(media):
            ctx.constats.avertir("media", f"{ou}/content", f"« {media} » n'est pas un type de média")


# -------------------------------------------------------------- sécurité --

def controler_securite(ctx: Contexte) -> None:
    """Exigences vers des schémas déclarés ; schémas inutilisés."""
    composants = ctx.racine.get("components")
    schemes = composants.get("securitySchemes") if isinstance(composants, dict) else None
    schemes = schemes if isinstance(schemes, dict) else {}
    utilises: set[str] = set()
    exigences = [("#/security", ctx.racine.get("security"))]
    exigences += [(emplacement_operation(z, c, m), o.get("security"))
                  for z, c, m, o, _ in iterer_operations(ctx)]
    for ou, liste in exigences:
        for exigence in liste if isinstance(liste, list) else ():
            for nom in exigence if isinstance(exigence, dict) else ():
                utilises.add(nom)
                if nom not in schemes:
                    ctx.constats.erreur("securite", ou, f"exigence de sécurité « {nom} » sans "
                                        "schéma déclaré dans components.securitySchemes")
    for nom, scheme in schemes.items():
        scheme = suivre_ref(ctx.racine, scheme)
        if not isinstance(scheme, dict) or not isinstance(scheme.get("type"), str):
            ctx.constats.erreur("securite", pointeur("components", "securitySchemes", nom),
                                "schéma de sécurité sans type")
        if nom not in utilises:
            ctx.constats.avertir("securite-inutilisee", pointeur("components", "securitySchemes", nom),
                                 "schéma de sécurité déclaré mais jamais exigé")


# -------------------------------------------------------------- références --

def cible_mapping(racine: Any, valeur: str) -> str:
    """Valeur de discriminator.mapping : nom de schéma ramené à sa $ref."""
    if valeur.startswith("#") or not MOTIF_NOM_COMPOSANT.match(valeur):
        return valeur
    composants = racine.get("components") if isinstance(racine, dict) else None
    schemas = composants.get("schemas") if isinstance(composants, dict) else None
    if isinstance(schemas, dict) and valeur in schemas or "." not in valeur:
        return "#/components/schemas/" + valeur.replace("~", "~0").replace("/", "~1")
    return valeur


def collecter_refs(racine: Any, noeud: Any, ou: str, carte_de_noms: bool,
                   sortie: list[tuple[str, str]], vus: set[int]) -> None:
    """Toutes les $ref (et cibles de discriminator.mapping) hors valeurs de données.

    Les $ref trouvées sous une extension « x-… » sont rangées avec un emplacement
    préfixé par « x: » : elles comptent pour l'accessibilité, pas pour la validité.
    """
    if id(noeud) in vus or not isinstance(noeud, (dict, list)):
        return
    vus.add(id(noeud))
    if isinstance(noeud, list):
        for index, element in enumerate(noeud):
            collecter_refs(racine, element, f"{ou}/{index}", False, sortie, vus)
        return
    for cle, valeur in noeud.items():
        lieu = f"{ou}/{str(cle).replace('~', '~0').replace('/', '~1')}"
        if carte_de_noms:
            collecter_refs(racine, valeur, lieu, False, sortie, vus)
        elif cle in CLES_DONNEES:
            continue
        elif str(cle).startswith("x-") and not ou.startswith("x:"):
            collecter_refs(racine, valeur, "x:" + lieu, False, sortie, vus)
        elif cle == "$ref" and isinstance(valeur, str):
            sortie.append((ou, valeur))
        elif cle == "mapping" and isinstance(valeur, dict):
            sortie.extend((lieu, cible_mapping(racine, v)) for v in valeur.values() if isinstance(v, str))
        elif (cle == "examples" and isinstance(valeur, list)) or (cle == "value" and "/examples/" in ou):
            continue
        else:
            collecter_refs(racine, valeur, lieu, cle in CARTES_DE_NOMS, sortie, vus)


def ancres_du_document(racine: Any) -> set[str]:
    """Valeurs de $anchor (3.1) présentes dans le document."""
    trouvees: set[str] = set()
    pile = [racine]
    vus: set[int] = set()
    while pile:
        noeud = pile.pop()
        if id(noeud) in vus:
            continue
        vus.add(id(noeud))
        if isinstance(noeud, dict):
            if isinstance(noeud.get("$anchor"), str):
                trouvees.add(noeud["$anchor"])
            pile.extend(noeud.values())
        elif isinstance(noeud, list):
            pile.extend(noeud)
    return trouvees


def controler_refs(ctx: Contexte) -> list[tuple[str, str]]:
    """Résout chaque $ref locale ; liste les externes ; rend toutes les refs."""
    refs: list[tuple[str, str]] = []
    collecter_refs(ctx.racine, ctx.racine, "#", False, refs, set())
    ancres = ancres_du_document(ctx.racine) if ctx.mineure >= 1 else set()
    for ou, ref in refs:
        if ou.startswith("x:"):
            continue
        if not est_ref_locale(ref):
            if ref not in ctx.refs_externes:
                ctx.refs_externes.append(ref)
            continue
        ctx.refs_locales += 1
        controler_une_ref(ctx, ou, ref, ancres)
    return refs


def controler_une_ref(ctx: Contexte, ou: str, ref: str, ancres: set[str]) -> None:
    """Une $ref locale : pointeur JSON, ancre 3.1, chaîne en boucle."""
    fragment = ref[1:]
    if fragment and not fragment.startswith("/"):
        if unquote(fragment) not in ancres:
            ctx.constats.erreur("ref", ou, f"$ref {ref} : ancre $anchor introuvable")
        return
    if not resoudre_pointeur(ctx.racine, fragment)[0]:
        ctx.constats.erreur("ref", ou, f"$ref {ref} ne désigne rien dans le document")
    elif chaine_en_boucle(ctx.racine, ref):
        ctx.constats.erreur("ref", ou, f"$ref {ref} : chaîne de références qui revient sur elle-même")


def chaine_en_boucle(racine: Any, ref: str) -> bool:
    """Vrai si une chaîne de $ref purement locales revient sur elle-même."""
    vues: set[str] = set()
    while ref.startswith("#/") and ref not in vues and len(vues) < LIMITE_CHAINE_REF:
        vues.add(ref)
        trouve, noeud = resoudre_pointeur(racine, ref[1:])
        if not trouve or not isinstance(noeud, dict) or not isinstance(noeud.get("$ref"), str):
            return False
        ref = noeud["$ref"]
    return ref in vues or len(vues) >= LIMITE_CHAINE_REF


def composant_vise(ref: str) -> tuple[str, str] | None:
    """(type, nom) du composant visé par « #/components/<type>/<nom>… »."""
    if not ref.startswith("#/components/"):
        return None
    morceaux = ref[len("#/components/"):].split("/")
    if len(morceaux) < 2:
        return None
    nom = unquote(morceaux[1]).replace("~1", "/").replace("~0", "~")
    return morceaux[0], nom


def controler_orphelins(ctx: Contexte, refs: list[tuple[str, str]]) -> int:
    """Composants inaccessibles depuis chemins et webhooks ; rend le nombre de schémas."""
    composants = ctx.racine.get("components")
    if not isinstance(composants, dict):
        return 0
    nb_schemas = len(composants.get("schemas") or {}) if isinstance(composants.get("schemas"), dict) else 0
    if not ctx.racine.get("paths") and not ctx.racine.get("webhooks"):
        return nb_schemas
    atteints = composants_atteints(ctx.racine, [r for o, r in refs if not o.startswith(
        ("#/components/", "x:#/components/"))])
    for type_composant in TYPES_COMPOSANTS:
        groupe = composants.get(type_composant)
        for nom in groupe if isinstance(groupe, dict) else ():
            if (type_composant, nom) not in atteints:
                ctx.constats.avertir("orphelin", pointeur("components", type_composant, nom),
                                     f"composant {type_composant}/{nom} jamais atteint depuis "
                                     "paths ou webhooks")
    return nb_schemas


def composants_atteints(racine: dict[str, Any], depart: list[str]) -> set[tuple[str, str]]:
    """Fermeture transitive des composants visés par des $ref."""
    atteints: set[tuple[str, str]] = set()
    a_voir = list(depart)
    while a_voir:
        cible = composant_vise(a_voir.pop())
        if cible is None or cible in atteints:
            continue
        atteints.add(cible)
        trouve, noeud = resoudre_pointeur(racine, "/" + "/".join(
            ("components", cible[0], cible[1].replace("~", "~0").replace("/", "~1"))))
        if trouve:
            internes: list[tuple[str, str]] = []
            collecter_refs(racine, noeud, "#", False, internes, set())
            a_voir.extend(r for _, r in internes)
    return atteints


# --------------------------------------------------------------- schémas --

def iterer_schemas(noeud: Any, ou: str, vus: set[int]) -> Iterator[tuple[str, dict[str, Any]]]:
    """Schéma et sous-schémas (sans suivre les $ref)."""
    if not isinstance(noeud, dict) or id(noeud) in vus:
        return
    vus.add(id(noeud))
    yield ou, noeud
    for mot in MOTS_SOUS_SCHEMAS:
        if isinstance(noeud.get(mot), dict):
            yield from iterer_schemas(noeud[mot], f"{ou}/{mot}", vus)
    for mot in LISTES_SOUS_SCHEMAS:
        for index, sous in enumerate(noeud.get(mot) or []) if isinstance(noeud.get(mot), list) else ():
            yield from iterer_schemas(sous, f"{ou}/{mot}/{index}", vus)
    for mot in CARTES_SOUS_SCHEMAS:
        for nom, sous in noeud[mot].items() if isinstance(noeud.get(mot), dict) else ():
            yield from iterer_schemas(sous, f"{ou}/{mot}/{nom.replace('~', '~0').replace('/', '~1')}", vus)


def racines_schemas(ctx: Contexte) -> Iterator[tuple[str, Any]]:
    """Schémas des composants, paramètres, contenus et en-têtes."""
    composants = ctx.racine.get("components")
    schemas = composants.get("schemas") if isinstance(composants, dict) else None
    for nom, schema in schemas.items() if isinstance(schemas, dict) else ():
        yield pointeur("components", "schemas", nom), schema
    pile: list[tuple[str, Any]] = [("#", ctx.racine)]
    vus: set[int] = set()
    while pile:
        ou, noeud = pile.pop()
        if id(noeud) in vus or not isinstance(noeud, (dict, list)):
            continue
        vus.add(id(noeud))
        if isinstance(noeud, list):
            pile.extend((f"{ou}/{i}", v) for i, v in enumerate(noeud))
            continue
        for cle, valeur in noeud.items():
            if cle == "schema" and isinstance(valeur, dict):
                yield f"{ou}/schema", valeur
            elif cle not in ("schemas", "example", "examples", "default", "enum") and not str(cle).startswith("x-"):
                pile.append((f"{ou}/{str(cle).replace('~', '~0').replace('/', '~1')}", valeur))


def controler_schemas(ctx: Contexte) -> None:
    """Cohérence légère des schémas selon la version."""
    vus: set[int] = set()
    for ou_racine, schema in racines_schemas(ctx):
        for ou, noeud in iterer_schemas(schema, ou_racine, vus):
            controler_un_schema(ctx, noeud, ou)


def controler_un_schema(ctx: Contexte, schema: dict[str, Any], ou: str) -> None:
    """type, items, required, nullable, exclusiveMinimum/Maximum, enum."""
    if "$ref" in schema and ctx.mineure == 0:
        return
    controler_type(ctx, schema, ou)
    requis = schema.get("required")
    proprietes = schema.get("properties")
    if isinstance(requis, list) and isinstance(proprietes, dict) and not any(
            k in schema for k in ("allOf", "anyOf", "oneOf", "$ref", "patternProperties")):
        for nom in requis:
            if nom not in proprietes:
                ctx.constats.avertir("schema", ou, f"required cite « {nom} » absent de properties")
    if "enum" in schema and isinstance(schema["enum"], list) and not schema["enum"]:
        ctx.constats.avertir("schema", ou, "enum vide : aucune valeur ne peut être valide")
    controler_mots_version(ctx, schema, ou)


def controler_type(ctx: Contexte, schema: dict[str, Any], ou: str) -> None:
    """Valeurs de type et items des tableaux."""
    genre = schema.get("type")
    admis = TYPES_SCHEMA_30 if ctx.mineure == 0 else TYPES_SCHEMA_31
    if isinstance(genre, list) and ctx.mineure == 0:
        ctx.constats.erreur("schema", ou, "type en liste interdit en 3.0 (utiliser nullable ou oneOf)")
    valeurs = genre if isinstance(genre, list) else [genre] if genre is not None else []
    for valeur in valeurs:
        if valeur not in admis:
            ctx.constats.erreur("schema", ou, f"type « {valeur} » inconnu")
    if ctx.mineure == 0 and genre == "array" and "items" not in schema:
        ctx.constats.erreur("schema", ou, "type array sans items (obligatoire en 3.0)")


def controler_mots_version(ctx: Contexte, schema: dict[str, Any], ou: str) -> None:
    """Mots-clés dont le sens a changé entre 3.0 et 3.1."""
    for mot in ("exclusiveMinimum", "exclusiveMaximum"):
        if mot not in schema:
            continue
        booleen = isinstance(schema[mot], bool)
        if ctx.mineure == 0 and not booleen:
            ctx.constats.erreur("schema", ou, f"{mot} doit être booléen en 3.0")
        if ctx.mineure >= 1 and booleen:
            ctx.constats.erreur("schema", ou, f"{mot} doit être un nombre en 3.1 (booléen en 3.0)")
    if ctx.mineure >= 1 and "nullable" in schema:
        ctx.constats.avertir("schema", ou, "nullable n'existe plus en 3.1 : ignoré "
                             "(utiliser type: [..., \"null\"])")


# ----------------------------------------------------- comparaison tierce --

def comparer_osv(document: dict[str, Any], mineure: int, valide: bool) -> dict[str, Any]:
    """Valide par openapi-spec-validator et compare le verdict."""
    classes = {0: "OpenAPIV30SpecValidator", 1: "OpenAPIV31SpecValidator", 2: "OpenAPIV32SpecValidator"}
    classe = getattr(module_osv, classes[mineure], module_osv.OpenAPIV31SpecValidator)
    try:
        erreurs = [str(getattr(e, "message", e))[:300] for e in classe(document).iter_errors()]
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        return {"bibliotheque": "openapi-spec-validator", "echec": f"{type(exc).__name__}: {exc}"[:300]}
    return {"bibliotheque": "openapi-spec-validator", "version": getattr(module_osv, "__version__", "?"),
            "valide": not erreurs, "erreurs_total": len(erreurs),
            "erreurs": erreurs[:LIMITE_ERREURS_TIERCES], "accord": (not erreurs) == valide}


def couper_recursion(limite: int, url: Any, recursions: Any = ()) -> dict[str, Any]:
    """Pour prance : un schéma récursif est légitime, la récursion est coupée sans erreur."""
    return {"x-recursion-coupee": limite}


def comparer_prance(document: dict[str, Any], refs_cassees: bool) -> dict[str, Any]:
    """Résolution des $ref internes par prance, comparée à la nôtre."""
    texte = json.dumps(document)
    try:
        analyseur = module_prance.ResolvingParser(
            spec_string=texte, lazy=True, backend="openapi-spec-validator",
            resolve_types=module_prance_resolver.RESOLVE_INTERNAL, strict=False,
            recursion_limit_handler=couper_recursion)
        analyseur.parse()
        resolu, message = True, ""
    except (module_prance.ValidationError, LookupError, ValueError, TypeError, RecursionError) as exc:
        resolu, message = False, f"{type(exc).__name__}: {exc}"[:300]
    return {"bibliotheque": "prance", "version": getattr(module_prance, "__version__", "?"),
            "accepte": resolu, "message": message, "accord_refs": resolu != refs_cassees}


def comparaisons_tierces(ctx: Contexte, sans_tiers: bool) -> list[dict[str, Any]]:
    """Comparaisons avec les bibliothèques présentes (refs locales seulement)."""
    if sans_tiers or (module_osv is None and module_prance is None):
        return []
    if ctx.refs_externes:
        return [{"bibliotheque": "openapi-spec-validator/prance", "non_lancee":
                 "références externes présentes : non lancées pour n'ouvrir ni réseau ni fichier voisin"}]
    valide = ctx.constats.compter(GRAVITE_ERREUR) == 0
    refs_cassees = any(c["regle"] == "ref" for c in ctx.constats.liste)
    resultats = []
    if module_osv is not None:
        resultats.append(comparer_osv(ctx.racine, ctx.mineure, valide))
    if module_prance is not None:
        resultats.append(comparer_prance(ctx.racine, refs_cassees))
    return resultats


# ------------------------------------------------------------ orchestration --

def version_mineure(document: Any, source: str) -> int | None:
    """Mineure 0/1/2 d'un document OpenAPI 3 ; None si ce n'en est pas un."""
    if not isinstance(document, dict):
        return None
    if "swagger" in document and "openapi" not in document:
        raise ErreurEntree(f"{source} : Swagger {document.get('swagger')} non pris en charge "
                           "(OpenAPI 3.0/3.1 seulement)")
    version = document.get("openapi")
    if version is None:
        return None
    correspondance = MOTIF_VERSION_LACHE.match(str(version))
    if not correspondance:
        raise ErreurEntree(f"{source} : openapi « {version} » n'est pas une version 3.0.x ou 3.1.x")
    return int(correspondance.group(1))


def valider_document(doc: Document, mineure: int, sans_tiers: bool) -> dict[str, Any]:
    """Applique toutes les règles à un document."""
    ctx = Contexte(doc.contenu, mineure, Constats())
    for doublon in doc.doublons:
        ctx.constats.erreur("cle-dupliquee", "#", f"{doublon} : la valeur retenue dépend du lecteur")
    controler_racine(ctx)
    controler_serveurs(ctx, ctx.racine.get("servers"), "#")
    controler_chemins(ctx)
    controler_operations(ctx)
    controler_securite(ctx)
    refs = controler_refs(ctx)
    nb_schemas = controler_orphelins(ctx, refs)
    controler_schemas(ctx)
    return resumer_document(doc, ctx, nb_schemas, comparaisons_tierces(ctx, sans_tiers))


def resumer_document(doc: Document, ctx: Contexte, nb_schemas: int,
                     comparaison: list[dict[str, Any]]) -> dict[str, Any]:
    """Résultat JSON d'un document."""
    erreurs = ctx.constats.compter(GRAVITE_ERREUR)
    ordre = sorted(ctx.constats.liste, key=lambda c: (c["gravite"] != GRAVITE_ERREUR, c["regle"]))
    return {"source": doc.source, "openapi": str(ctx.racine.get("openapi")), "moteur": doc.moteur,
            "valide": erreurs == 0, "erreurs": erreurs,
            "avertissements": ctx.constats.compter(GRAVITE_AVERTISSEMENT),
            "chemins": len(ctx.racine.get("paths") or {}) if isinstance(ctx.racine.get("paths"), dict) else 0,
            "operations": ctx.operations, "schemas_composants": nb_schemas,
            "refs_locales": ctx.refs_locales, "refs_externes": ctx.refs_externes[:LIMITE_EXAMINES],
            "constats_total": len(ordre), "constats": ordre[:LIMITE_CONSTATS],
            "comparaison": comparaison}


def charger_sources(args: argparse.Namespace) -> tuple[list[tuple[Document, int]], list[dict[str, str]]]:
    """Documents OpenAPI 3 retenus et fichiers ignorés (dossiers seulement)."""
    retenus: list[tuple[Document, int]] = []
    ignores: list[dict[str, str]] = []
    if args.texte is not None:
        doc = charger_texte(args.texte, "<texte>", args.stdlib)
        retenus.append((doc, exiger_openapi(doc)))
    racine = Path(args.racine) if args.racine else None
    for chemin, explicite in lister_fichiers(args.chemins, racine):
        try:
            doc = charger_texte(decoder(lire_fichier(chemin), str(chemin)), str(chemin), args.stdlib)
            mineure = version_mineure(doc.contenu, doc.source) if not explicite else exiger_openapi(doc)
        except ErreurEntree as exc:
            if explicite:
                raise
            ignores.append({"source": str(chemin), "raison": str(exc)[:200]})
            continue
        if mineure is not None:
            retenus.append((doc, mineure))
    return retenus, ignores


def exiger_openapi(doc: Document) -> int:
    """Version mineure, ou erreur si le document n'est pas OpenAPI 3."""
    mineure = version_mineure(doc.contenu, doc.source)
    if mineure is None:
        raise ErreurEntree(f"{doc.source} : pas de champ openapi, ce n'est pas un document OpenAPI 3")
    return mineure


def analyser(args: argparse.Namespace) -> dict[str, Any]:
    """Charge, valide, assemble."""
    retenus, ignores = charger_sources(args)
    resultats = [valider_document(doc, mineure, args.sans_comparaison) for doc, mineure in retenus]
    moteurs = sorted({r["moteur"] for r in resultats} | {
        c["bibliotheque"] for r in resultats for c in r["comparaison"] if "non_lancee" not in c})
    noms = [r["source"] for r in resultats]
    return {"denominateur": len(resultats), "examines": noms[:LIMITE_EXAMINES],
            "examines_tronques": len(noms) > LIMITE_EXAMINES, "moteur": "+".join(moteurs) or "stdlib",
            "documents_invalides": sum(1 for r in resultats if not r["valide"]),
            "erreurs_total": sum(r["erreurs"] for r in resultats),
            "avertissements_total": sum(r["avertissements"] for r in resultats),
            "documents": resultats, "ignores_total": len(ignores), "ignores": ignores[:LIMITE_EXAMINES]}


def lire_contrat() -> dict[str, str]:
    """Découpe la docstring du module en sections du contrat de mesure."""
    sections: dict[str, list[str]] = {}
    courant = ""
    for ligne in (__doc__ or "").splitlines():
        if ligne.strip() in TITRES_CONTRAT and not ligne.startswith(" "):
            courant = ligne.strip()
            sections[courant] = []
        elif courant:
            sections[courant].append(ligne.strip())
    return {cle: " ".join(m for m in val if m) for cle, val in sections.items()}


# ----------------------------------------------------------------- sortie --

def imprimer_humain(res: dict[str, Any]) -> None:
    """Sortie lisible."""
    print(f"{res['denominateur']} document(s) OpenAPI examiné(s) ; {res['documents_invalides']} "
          f"invalide(s) ; {res['erreurs_total']} erreur(s), {res['avertissements_total']} "
          f"avertissement(s) ; moteur {res['moteur']}")
    for doc in res["documents"]:
        etat = "VALIDE" if doc["valide"] else "INVALIDE"
        print(f"\n{doc['source']} (openapi {doc['openapi']}) : {etat} — {doc['operations']} opération(s), "
              f"{doc['refs_locales']} $ref locale(s), {len(doc['refs_externes'])} externe(s)")
        for constat in doc["constats"][:200]:
            print(f"  [{constat['gravite']}] {constat['regle']} {constat['emplacement']} : "
                  f"{constat['message']}")
        for comp in doc["comparaison"]:
            print(f"  contrôle {comp['bibliotheque']} : " + presenter_comparaison(comp))


def presenter_comparaison(comp: dict[str, Any]) -> str:
    """Résumé d'une comparaison tierce."""
    if "non_lancee" in comp:
        return comp["non_lancee"]
    if "echec" in comp:
        return "échec " + comp["echec"]
    if "accord" in comp:
        return (f"{'valide' if comp['valide'] else 'invalide'} ({comp['erreurs_total']} erreur(s)), "
                f"{'accord' if comp['accord'] else 'DÉSACCORD'} avec l'outil")
    return f"{'accepte' if comp['accepte'] else 'refuse : ' + comp['message']}"


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Valide des spécifications OpenAPI 3.0/3.1 (JSON ou YAML) : champs "
                    "obligatoires, $ref locales, paramètres de chemin, operationId, réponses, "
                    "sécurité, composants orphelins.",
        epilog="Exemples : valider_openapi.py api/openapi.yaml --json\n"
               "           valider_openapi.py specs/ --strict\n"
               "Codes : 0 valide ; 1 au moins une erreur (ou un avertissement avec --strict) ; "
               "2 entrée invalide ; 3 aucun document OpenAPI à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parseur.add_argument("chemins", nargs="*", help="fichiers .json/.yaml/.yml ou dossiers (récursif)")
    parseur.add_argument("--texte", help="spécification en ligne (JSON ou YAML)")
    parseur.add_argument("--strict", action="store_true", help="les avertissements donnent aussi le code 1")
    parseur.add_argument("--stdlib", action="store_true",
                         help="lire le YAML avec le lecteur stdlib même si PyYAML est installée")
    parseur.add_argument("--sans-comparaison", action="store_true",
                         help="ne pas lancer openapi-spec-validator ni prance même si installées")
    parseur.add_argument("--racine", help="base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    return parseur


def signaler_bibliotheques(args: argparse.Namespace) -> None:
    """Une ligne sur stderr par bibliothèque optionnelle absente."""
    if module_yaml is None:
        print("PyYAML absente : YAML lu par le lecteur stdlib (sous-ensemble documenté)", file=sys.stderr)
    if module_osv is None and not args.sans_comparaison:
        print("openapi-spec-validator absente : pas de contre-validation tierce", file=sys.stderr)
    if module_prance is None and not args.sans_comparaison:
        print("prance absente : pas de contre-résolution tierce des $ref", file=sys.stderr)


def code_sortie(res: dict[str, Any], strict: bool) -> int:
    """1 si erreur (ou avertissement en mode strict), 0 sinon."""
    if res["erreurs_total"] or (strict and res["avertissements_total"]):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée."""
    parseur = construire_parseur()
    args = parseur.parse_args(argv)
    if not args.chemins and args.texte is None:
        parseur.print_usage(sys.stderr)
        print("erreur : donner un fichier, un dossier ou --texte", file=sys.stderr)
        return 2
    signaler_bibliotheques(args)
    try:
        res = analyser(args)
    except ErreurEntree as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 2
    except RecursionError:
        print("erreur : document trop profondément imbriqué", file=sys.stderr)
        return 2
    if res["denominateur"] == 0:
        print("dénominateur nul : aucun document OpenAPI 3 trouvé, rien à examiner", file=sys.stderr)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        return 3
    res["contrat"] = lire_contrat()
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        imprimer_humain(res)
    return code_sortie(res, args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
