"""Dire si un XML peut être lu sans danger avant de le confier à un analyseur, et décrire sa forme.

Mesuré dans cette session (Python 3.14.7, expat 2.8.5) : xml.etree.ElementTree.fromstring
déploie sans erreur un document de 395 octets à six niveaux d'entités imbriquées (x10 chacun)
en 3 000 000 de caractères ; seule la protection d'amplification d'expat arrête le septième
niveau (450 octets). defusedxml 0.7.1 refuse au contraire toute déclaration d'entité, même
une entité interne inoffensive (auteur = « Jean ») : aucun des deux ne dit pourquoi.

QUESTION
    Ce XML peut-il être lu sans danger (entité externe dite XXE, bombe d'entités), et quelle
    est sa structure (racine, espaces de noms, éléments, profondeur) ?
MESURE
    Un passage pyexpat en flux, qui ne charge jamais rien d'externe (tout appel à une DTD ou
    à une entité externe est intercepté et refusé) mais déploie les entités paramètres
    internes, pour voir les déclarations qu'elles cacheraient. Il relève le doctype et ses
    identifiants system/public, chaque déclaration d'entité (interne, externe, paramètre,
    non analysée), compte les références aux entités internes dans le contenu sans les
    déployer, calcule statiquement le déploiement de chaque entité (taille, nombre de
    substitutions, profondeur d'imbrication, cycles) et le compare à des plafonds. Si une
    entité dépasse un plafond, l'analyse s'arrête à la fin du doctype, avant tout déploiement.
    Ensuite : racine, espaces de noms déclarés, comptes d'éléments, profondeur, attributs,
    commentaires. Conversion JSON facultative (convention xmltodict : @attribut, #text).
    Comparaison facultative avec defusedxml (refuse-t-il aussi ?).
HYPOTHÈSES
    Le lecteur visé est un analyseur conforme qui déploie les entités internes et peut
    résoudre les entités externes (cas de nombreux analyseurs hors Python). Les plafonds par
    défaut (profondeur 5, 10 000 substitutions, 1 000 000 de caractères déployés) séparent
    l'usage légitime de l'attaque ; ils se règlent par option.
LIMITES
    Les références d'entités situées dans des valeurs d'attributs sont déployées par expat
    lui-même (sous sa protection d'amplification, abaissée ici à 1 Mio) et ne sont pas
    comptées une à une. Les éléments apportés par une entité ne figurent pas dans la
    structure. Une DTD externe n'est jamais lue : les entités qu'elle déclarerait sont
    signalées comme ignorées, pas évaluées. En dossier, seuls les fichiers aux extensions
    XML connues sont lus, les liens symboliques et les dossiers .git, node_modules, etc.
    sont sautés. La conversion JSON charge le document en mémoire (plafond 16 Mio).
CONTRE-EXEMPLES
    Constaté : un fichier plist d'Apple, dont le doctype désigne la DTD publique
    http://www.apple.com/DTDs/PropertyList-1.0.dtd, est rendu sûr (code 0) avec un simple
    avertissement de DTD externe, alors qu'un analyseur qui charge les DTD externes
    émettrait une requête vers cette URL. Il faut --strict pour le classer dangereux. Inversement, un document à entités internes
    bornées est dit sûr alors que defusedxml le refuse.
INVOCATION
    {outil} --texte '<r xmlns="urn:exemple"><a k="1">t</a><a/></r>' --json
    {outil} {fichier} --json
DOMAINE
    Fichiers XML locaux (configurations, SVG, plist, flux, exports, réponses d'API
    enregistrées) avant de les confier à un analyseur, quel que soit son langage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import pyexpat

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

try:
    import defusedxml
    import defusedxml.ElementTree as defused_et
except ImportError:
    defusedxml = None
    defused_et = None

try:
    import xmltodict
except ImportError:
    xmltodict = None

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", INTITULE_HYPOTHESES, "LIMITES",
             "CONTRE-EXEMPLES", INTITULE_INVOCATION, "DOMAINE")

EXTENSIONS_XML = frozenset({
    ".xml", ".xsd", ".xsl", ".xslt", ".svg", ".rss", ".atom", ".rdf", ".plist",
    ".xhtml", ".kml", ".gpx", ".wsdl", ".xlf", ".xliff", ".resx", ".csproj",
    ".vbproj", ".fsproj", ".props", ".targets", ".nuspec", ".pom", ".xul", ".opml",
    ".dita", ".docbook", ".tmx", ".sitemap",
})
DOSSIERS_IGNORES = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__",
                              ".venv", "venv", ".tox", ".mypy_cache"})
ENTITES_PREDEFINIES = frozenset({"amp", "lt", "gt", "quot", "apos"})
MOTIF_REFERENCE = re.compile(r"&([^\s&;#%<>\"']+);")
PLAFOND_EXAMINES = 200
PLAFOND_NOMS_DISTINCTS = 10_000
PLAFOND_ELEMENTS_AFFICHES = 20
PROFONDEUR_CONVERSION_MAX = 400
SEUIL_AMPLIFICATION_EXPAT = 1 << 20
NOM_OUTIL = "lire_xml_sur"
ENCODAGE_SORTIE = "utf-8"


class ArretAnalyse(Exception):
    """Interrompt pyexpat dès qu'une bombe d'entités est établie, avant tout déploiement."""


class EntreeInvalide(Exception):
    """Entrée inutilisable (chemin absent, illisible) : code 2."""


@dataclass(frozen=True)
class Plafonds:
    """Seuils au-delà desquels le déploiement des entités est jugé dangereux."""

    profondeur: int
    expansions: int
    taille: int
    strict: bool


@dataclass(frozen=True)
class Source:
    """Une entrée à examiner : fichier, flux standard ou texte en ligne."""

    nom: str
    chemin: Path | None = None
    texte: str | None = None


@dataclass
class Collecteur:
    """Reçoit les évènements pyexpat d'un document et en garde ce qu'il faut mesurer."""

    plafonds: Plafonds
    doctype: dict[str, Any] | None = None
    declaration: dict[str, Any] | None = None
    internes: dict[str, str] = field(default_factory=dict)
    parametres_internes: list[str] = field(default_factory=list)
    externes: list[dict[str, Any]] = field(default_factory=list)
    references: Counter = field(default_factory=Counter)
    ignorees: Counter = field(default_factory=Counter)
    appels_externes: list[dict[str, Any]] = field(default_factory=list)
    mesures: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    cycles: list[str] = field(default_factory=list)
    bombe: list[str] = field(default_factory=list)
    racine: dict[str, Any] | None = None
    espaces: dict[str, str] = field(default_factory=dict)
    elements: Counter = field(default_factory=Counter)
    autres_elements: int = 0
    nb_elements: int = 0
    profondeur: int = 0
    profondeur_max: int = 0
    attributs: int = 0
    commentaires: int = 0
    instructions: int = 0
    cdata: int = 0

    def brancher(self, analyseur: Any) -> None:
        """Pose les gestionnaires ; le DefaultHandler coupe le déploiement des entités internes."""
        analyseur.XmlDeclHandler = self.declaration_xml
        analyseur.StartDoctypeDeclHandler = self.debut_doctype
        analyseur.EndDoctypeDeclHandler = self.fin_doctype
        analyseur.EntityDeclHandler = self.declaration_entite
        analyseur.ExternalEntityRefHandler = self.reference_externe
        analyseur.SkippedEntityHandler = self.entite_ignoree
        analyseur.StartElementHandler = self.debut_element
        analyseur.EndElementHandler = self.fin_element
        analyseur.StartNamespaceDeclHandler = self.espace_de_noms
        analyseur.CommentHandler = self.commentaire
        analyseur.ProcessingInstructionHandler = self.instruction
        analyseur.StartCdataSectionHandler = self.debut_cdata
        analyseur.DefaultHandler = self.defaut

    def declaration_xml(self, version: str, encodage: str | None, autonome: int) -> None:
        self.declaration = {"version": version, "encodage": encodage, "autonome": autonome}

    def debut_doctype(self, nom: str, systeme: str | None, public: str | None,
                      sous_ensemble: int) -> None:
        self.doctype = {"nom": nom, "system": systeme, "public": public,
                        "sous_ensemble_interne": bool(sous_ensemble)}

    def fin_doctype(self) -> None:
        """Fin de la DTD interne : toutes les entités sont connues, on mesure avant le corps."""
        self.mesures, self.cycles = mesurer_entites(self.internes)
        self.bombe = entites_hors_plafond(self.mesures, self.cycles, self.plafonds)
        if self.bombe:
            raise ArretAnalyse("; ".join(self.bombe))

    def declaration_entite(self, nom: str, parametre: int, valeur: str | None, base: str | None,
                           systeme: str | None, public: str | None, notation: str | None) -> None:
        if valeur is not None and not parametre:
            self.internes[nom] = valeur
        elif valeur is not None:
            self.parametres_internes.append(nom)
        else:
            self.externes.append({"nom": nom, "parametre": bool(parametre), "system": systeme,
                                  "public": public, "notation": notation})

    def reference_externe(self, contexte: str | None, base: str | None, systeme: str | None,
                          public: str | None) -> int:
        """Appel d'une entité externe : consigné, jamais résolu (1 = continuer sans charger)."""
        self.appels_externes.append({"system": systeme, "public": public})
        return 1

    def entite_ignoree(self, nom: str, parametre: int) -> None:
        if not parametre and nom in self.internes:
            self.references[nom] += 1
        else:
            self.ignorees[("%" if parametre else "&") + nom] += 1

    def debut_element(self, nom: str, attributs: dict[str, str]) -> None:
        qualifie, uri = nom_qualifie(nom)
        if self.racine is None:
            self.racine = {"nom": qualifie, "espace": uri}
        self.nb_elements += 1
        self.attributs += len(attributs)
        self.profondeur += 1
        self.profondeur_max = max(self.profondeur_max, self.profondeur)
        if qualifie in self.elements or len(self.elements) < PLAFOND_NOMS_DISTINCTS:
            self.elements[qualifie] += 1
        else:
            self.autres_elements += 1

    def fin_element(self, nom: str) -> None:
        self.profondeur -= 1

    def espace_de_noms(self, prefixe: str | None, uri: str | None) -> None:
        self.espaces.setdefault(prefixe or "", uri or "")

    def commentaire(self, texte: str) -> None:
        self.commentaires += 1

    def instruction(self, cible: str, donnees: str) -> None:
        self.instructions += 1

    def debut_cdata(self) -> None:
        self.cdata += 1

    def defaut(self, donnees: str) -> None:
        """Présence requise : sans elle, expat déploierait les entités internes du contenu."""


def nom_qualifie(nom: str) -> tuple[str, str | None]:
    """Recompose « préfixe:local » à partir de la forme « uri local préfixe » de pyexpat."""
    morceaux = nom.split(" ")
    if len(morceaux) == 3:
        return f"{morceaux[2]}:{morceaux[1]}", morceaux[0]
    if len(morceaux) == 2:
        return morceaux[1], morceaux[0]
    return nom, None


def references_de(valeur: str) -> tuple[list[str], int]:
    """Références d'entités générales d'une valeur, et longueur littérale une fois retirées."""
    noms = MOTIF_REFERENCE.findall(valeur)
    reste = len(MOTIF_REFERENCE.sub("", valeur))
    predefinies = sum(1 for nom in noms if nom in ENTITES_PREDEFINIES)
    return [nom for nom in noms if nom not in ENTITES_PREDEFINIES], reste + predefinies


def mesurer_entites(valeurs: dict[str, str]) -> tuple[dict[str, tuple[int, int, int]], list[str]]:
    """Taille déployée, substitutions et profondeur de chaque entité, sans rien déployer."""
    graphe = {nom: references_de(valeur) for nom, valeur in valeurs.items()}
    mesures: dict[str, tuple[int, int, int]] = {}
    cycles: set[str] = set()
    for depart in graphe:
        if depart not in mesures:
            parcourir_entite(depart, graphe, mesures, cycles)
    return mesures, sorted(cycles)


def parcourir_entite(depart: str, graphe: dict[str, tuple[list[str], int]],
                     mesures: dict[str, tuple[int, int, int]], cycles: set[str]) -> None:
    """Parcours en profondeur itératif (pas de récursion Python : une chaîne peut être longue)."""
    pile: list[tuple[str, Iterator[str]]] = [(depart, iter(graphe[depart][0]))]
    en_cours = {depart}
    while pile:
        nom, suite = pile[-1]
        enfant = next(suite, None)
        if enfant is None:
            pile.pop()
            en_cours.discard(nom)
            mesures[nom] = combiner_mesures(graphe[nom], mesures)
        elif enfant in en_cours:
            cycles.add(enfant)
        elif enfant in graphe and enfant not in mesures:
            en_cours.add(enfant)
            pile.append((enfant, iter(graphe[enfant][0])))


def combiner_mesures(noeud: tuple[list[str], int],
                     mesures: dict[str, tuple[int, int, int]]) -> tuple[int, int, int]:
    """Une entité vaut son texte littéral plus le déploiement de chaque référence connue."""
    references, litteral = noeud
    connues = [mesures[nom] for nom in references if nom in mesures]
    taille = litteral + sum(m[0] for m in connues)
    expansions = sum(1 + m[1] for m in connues)
    profondeur = 1 + max((m[2] for m in connues), default=0)
    return taille, expansions, profondeur


def entites_hors_plafond(mesures: dict[str, tuple[int, int, int]], cycles: list[str],
                         plafonds: Plafonds) -> list[str]:
    """Raisons de bombe portées par une seule entité (avant même d'être référencée)."""
    raisons = [f"entité récursive &{nom};" for nom in cycles]
    for nom, (taille, expansions, profondeur) in mesures.items():
        if profondeur > plafonds.profondeur:
            raisons.append(f"&{nom}; imbrique {profondeur} niveaux (plafond {plafonds.profondeur})")
        elif expansions > plafonds.expansions:
            raisons.append(f"&{nom}; demande {expansions} substitutions (plafond {plafonds.expansions})")
        elif taille > plafonds.taille:
            raisons.append(f"&{nom}; se déploie en {taille} caractères (plafond {plafonds.taille})")
    return raisons


def creer_analyseur(collecteur: Collecteur) -> Any:
    """Analyseur pyexpat : entités paramètres internes lues (les externes passent par
    reference_externe, qui refuse), protection d'amplification abaissée."""
    analyseur = pyexpat.ParserCreate(namespace_separator=" ")
    analyseur.namespace_prefixes = True
    analyseur.SetParamEntityParsing(pyexpat.XML_PARAM_ENTITY_PARSING_ALWAYS)
    if hasattr(analyseur, "SetBillionLaughsAttackProtectionActivationThreshold"):
        analyseur.SetBillionLaughsAttackProtectionActivationThreshold(SEUIL_AMPLIFICATION_EXPAT)
    collecteur.brancher(analyseur)
    return analyseur


def lancer_analyse(analyseur: Any, source: Source) -> int:
    """Nourrit l'analyseur en flux ; rend le nombre d'octets (ou de caractères) lus."""
    if source.texte is not None:
        analyseur.Parse(source.texte, True)
        return len(source.texte.encode(ENCODAGE_SORTIE))
    if source.chemin is None:
        lecteur = LecteurCompteur(sys.stdin.buffer)
        analyseur.ParseFile(lecteur)
        return lecteur.octets
    with source.chemin.open("rb") as flux:
        analyseur.ParseFile(flux)
    return source.chemin.stat().st_size


@dataclass
class LecteurCompteur:
    """Enveloppe un flux binaire et compte les octets que pyexpat y lit."""

    flux: Any
    octets: int = 0

    def read(self, taille: int = -1) -> bytes:
        bloc = self.flux.read(taille)
        self.octets += len(bloc)
        return bloc


def code_erreur(nom_message: str) -> int | None:
    """Code numérique d'une erreur expat d'après son message symbolique, s'il existe."""
    message = getattr(pyexpat.errors, nom_message, None)
    return pyexpat.errors.codes.get(message) if message else None


def analyser_source(source: Source, plafonds: Plafonds) -> dict[str, Any]:
    """Analyse complète d'une source : dangers, avertissements, déploiement, structure."""
    collecteur = Collecteur(plafonds)
    analyseur = creer_analyseur(collecteur)
    resultat: dict[str, Any] = {"chemin": source.nom, "erreur": None, "arret_anticipe": False}
    octets = 0
    try:
        octets = lancer_analyse(analyseur, source)
    except ArretAnalyse:
        resultat["arret_anticipe"] = True
    except pyexpat.ExpatError as exc:
        resultat["erreur"] = decrire_erreur(exc)
    resultat["octets"] = octets
    resultat.update(bilan_entites(collecteur, octets))
    resultat["structure"] = bilan_structure(collecteur)
    resultat["dangers"] = lister_dangers(collecteur, resultat, plafonds)
    resultat["avertissements"] = lister_avertissements(collecteur, plafonds)
    resultat["verdict"] = verdict_de(resultat)
    return resultat


def decrire_erreur(exc: pyexpat.ExpatError) -> dict[str, Any]:
    """Erreur expat lisible ; distingue ses protections (amplification, allocation) d'un défaut de forme."""
    protections = {code_erreur("XML_ERROR_AMPLIFICATION_LIMIT_BREACH"), code_erreur("XML_ERROR_NO_MEMORY")}
    amplification = exc.code in protections
    return {"message": pyexpat.ErrorString(exc.code), "ligne": exc.lineno,
            "colonne": exc.offset, "amplification": amplification}


def bilan_entites(collecteur: Collecteur, octets: int) -> dict[str, Any]:
    """Entités déclarées et déploiement total qu'exigerait le document."""
    total_taille = sum(n * collecteur.mesures.get(nom, (0, 0, 0))[0]
                       for nom, n in collecteur.references.items())
    total_expansions = sum(n * (1 + collecteur.mesures.get(nom, (0, 0, 0))[1])
                           for nom, n in collecteur.references.items())
    profondeur = max((collecteur.mesures.get(nom, (0, 0, 0))[2] for nom in collecteur.references),
                     default=0)
    internes = [{"nom": nom, "taille_deployee": m[0], "substitutions": m[1], "profondeur": m[2]}
                for nom, m in sorted(collecteur.mesures.items())]
    return {
        "doctype": collecteur.doctype,
        "declaration_xml": collecteur.declaration,
        "entites": {"internes": internes, "externes": collecteur.externes,
                    "parametres_internes": collecteur.parametres_internes[:PLAFOND_ELEMENTS_AFFICHES],
                    "cycles": collecteur.cycles,
                    "ignorees": dict(collecteur.ignorees.most_common(PLAFOND_ELEMENTS_AFFICHES))},
        "deploiement": {"references_contenu": sum(collecteur.references.values()),
                        "substitutions": total_expansions, "taille_deployee": total_taille,
                        "profondeur": profondeur,
                        "amplification": round(total_taille / octets, 2) if octets else None},
    }


def bilan_structure(collecteur: Collecteur) -> dict[str, Any]:
    """Racine, espaces de noms et comptes d'éléments relevés en flux."""
    return {
        "racine": collecteur.racine,
        "espaces_de_noms": collecteur.espaces,
        "elements": collecteur.nb_elements,
        "elements_distincts": len(collecteur.elements) + (1 if collecteur.autres_elements else 0),
        "plus_frequents": dict(collecteur.elements.most_common(PLAFOND_ELEMENTS_AFFICHES)),
        "profondeur_max": collecteur.profondeur_max,
        "attributs": collecteur.attributs,
        "commentaires": collecteur.commentaires,
        "instructions_traitement": collecteur.instructions,
        "sections_cdata": collecteur.cdata,
    }


def lister_dangers(collecteur: Collecteur, resultat: dict[str, Any],
                   plafonds: Plafonds) -> list[dict[str, str]]:
    """Constructions qui rendent la lecture dangereuse pour un analyseur conforme."""
    dangers = [danger_externe(entite) for entite in collecteur.externes]
    dangers += [{"type": "bombe_entites", "detail": raison} for raison in collecteur.bombe]
    deploiement = resultat["deploiement"]
    if deploiement["substitutions"] > plafonds.expansions:
        dangers.append({"type": "bombe_entites", "detail":
                        f"{deploiement['substitutions']} substitutions dans le document "
                        f"(plafond {plafonds.expansions})"})
    elif deploiement["taille_deployee"] > plafonds.taille:
        dangers.append({"type": "bombe_entites", "detail":
                        f"{deploiement['taille_deployee']} caractères déployés "
                        f"(plafond {plafonds.taille})"})
    declare = collecteur.internes or collecteur.parametres_internes
    if resultat["erreur"] and resultat["erreur"]["amplification"] and declare:
        dangers.append({"type": "bombe_entites", "detail": "déploiement arrêté par une protection "
                        f"d'expat ({resultat['erreur']['message']})"})
    if plafonds.strict:
        dangers += [dict(a, type=a["type"] + "_strict") for a in avertissements_stricts(collecteur)]
    return dangers


def danger_externe(entite: dict[str, Any]) -> dict[str, str]:
    """Une entité externe déclarée : lecture de fichier local ou requête sortante possible."""
    genre = "entite_parametre_externe" if entite["parametre"] else "entite_externe"
    cible = entite["system"] or entite["public"] or "?"
    signe = "%" if entite["parametre"] else "&"
    return {"type": genre, "detail": f"{signe}{entite['nom']}; -> {cible}"}


def avertissements_stricts(collecteur: Collecteur) -> list[dict[str, str]]:
    """Ce que --strict range parmi les dangers (position de defusedxml)."""
    sortie = []
    doctype = collecteur.doctype or {}
    if doctype.get("system") or doctype.get("public"):
        sortie.append({"type": "dtd_externe",
                       "detail": f"DTD externe non lue : {doctype.get('system') or doctype.get('public')}"})
    if collecteur.internes:
        sortie.append({"type": "entites_internes",
                       "detail": f"{len(collecteur.internes)} entité(s) interne(s) déclarée(s)"})
    return sortie


def lister_avertissements(collecteur: Collecteur, plafonds: Plafonds) -> list[dict[str, str]]:
    """Points à connaître qui ne rendent pas la lecture dangereuse."""
    sortie = [] if plafonds.strict else avertissements_stricts(collecteur)
    if collecteur.ignorees:
        noms = ", ".join(list(collecteur.ignorees)[:5])
        sortie.append({"type": "entites_ignorees",
                       "detail": f"références non résolues (DTD non lue) : {noms}"})
    if collecteur.appels_externes:
        sortie.append({"type": "appel_externe_bloque",
                       "detail": f"{len(collecteur.appels_externes)} appel(s) d'entité externe non résolu(s)"})
    return sortie


def verdict_de(resultat: dict[str, Any]) -> str:
    """dangereux > mal_forme > sur."""
    if resultat["dangers"]:
        return "dangereux"
    if resultat["erreur"]:
        return "mal_forme"
    return "sur"


def comparer_defusedxml(source: Source, plafond_octets: int) -> dict[str, Any] | None:
    """Refus de defusedxml sur la même entrée (comparaison, jamais décision)."""
    if defused_et is None or (source.chemin is None and source.texte is None):
        return None
    if source.chemin is not None and source.chemin.stat().st_size > plafond_octets:
        return {"compare": False, "raison": f"fichier au-delà de {plafond_octets} octets"}
    try:
        if source.texte is not None:
            defused_et.fromstring(source.texte)
        else:
            defused_et.parse(str(source.chemin))
    except (defusedxml.DefusedXmlException, defused_et.ParseError, ValueError) as exc:
        return {"compare": True, "refus": True, "exception": type(exc).__name__, "detail": str(exc)[:200]}
    return {"compare": True, "refus": False}


def convertir(source: Source, plafond_octets: int) -> dict[str, Any]:
    """Conversion JSON (convention xmltodict) d'un document déjà jugé sûr."""
    if source.chemin is None and source.texte is None:
        return {"converti": False, "raison": "flux standard déjà consommé par l'analyse"}
    donnees: bytes | str = source.texte if source.texte is not None else b""
    if source.chemin is not None:
        if source.chemin.stat().st_size > plafond_octets:
            return {"converti": False, "raison": f"fichier au-delà de {plafond_octets} octets"}
        donnees = source.chemin.read_bytes()
    note = None
    if xmltodict is not None:
        try:
            return {"converti": True, "moteur": "xmltodict", "valeur": xmltodict.parse(donnees)}
        except (ValueError, pyexpat.ExpatError) as exc:
            note = f"xmltodict a refusé ({exc}) ; repli stdlib"
    try:
        valeur = convertir_stdlib(donnees)
    except pyexpat.ExpatError as exc:
        return {"converti": False, "raison": f"conversion impossible : {pyexpat.ErrorString(exc.code)}"}
    return {"converti": True, "moteur": "stdlib", "note": note, "valeur": valeur}


def conversion_bornee(source: Source, resultat: dict[str, Any], plafond_octets: int) -> dict[str, Any]:
    """Refuse de convertir un document trop profond pour l'encodeur JSON (récursif)."""
    profondeur = resultat["structure"]["profondeur_max"]
    if profondeur > PROFONDEUR_CONVERSION_MAX:
        return {"converti": False, "raison": f"profondeur {profondeur} > {PROFONDEUR_CONVERSION_MAX}"}
    return convertir(source, plafond_octets)


@dataclass
class Convertisseur:
    """Réplique de l'algorithme de xmltodict.parse (réglages par défaut) sur pyexpat."""

    pile: list[tuple[Any, list[str]]] = field(default_factory=list)
    item: Any = None
    texte: list[str] = field(default_factory=list)

    def debut(self, nom: str, attributs: list[str]) -> None:
        self.pile.append((self.item, self.texte))
        paires = zip(attributs[0::2], attributs[1::2])
        self.item = {"@" + cle: valeur for cle, valeur in paires} or None
        self.texte = []

    def fin(self, nom: str) -> None:
        donnees = "".join(self.texte).strip() or None if self.texte else None
        item = self.item
        self.item, self.texte = self.pile.pop()
        if item is not None and donnees:
            item = ajouter(item, "#text", donnees)
        self.item = ajouter(self.item, nom, item if item is not None else donnees)

    def caracteres(self, donnees: str) -> None:
        self.texte.append(donnees)


def ajouter(item: Any, cle: str, valeur: Any) -> dict[str, Any]:
    """Ajoute une valeur ; une clé répétée devient une liste (comme xmltodict.push_data)."""
    item = {} if item is None else item
    if cle not in item:
        item[cle] = valeur
    elif isinstance(item[cle], list):
        item[cle].append(valeur)
    else:
        item[cle] = [item[cle], valeur]
    return item


def convertir_stdlib(donnees: bytes | str) -> Any:
    """Conversion sans espaces de noms ; entités internes déployées (document déjà jugé sûr)."""
    convertisseur = Convertisseur()
    analyseur = pyexpat.ParserCreate()
    analyseur.ordered_attributes = True
    analyseur.buffer_text = True
    analyseur.SetParamEntityParsing(pyexpat.XML_PARAM_ENTITY_PARSING_ALWAYS)
    analyseur.ExternalEntityRefHandler = lambda *_args: 1
    analyseur.StartElementHandler = convertisseur.debut
    analyseur.EndElementHandler = convertisseur.fin
    analyseur.CharacterDataHandler = convertisseur.caracteres
    analyseur.Parse(donnees, True)
    return convertisseur.item


def lister_fichiers(dossier: Path, plafond: int) -> tuple[list[Path], bool]:
    """Fichiers XML d'un dossier (récursif, sans suivre les liens), triés, plafonnés."""
    trouves: list[Path] = []
    for courant, sous_dossiers, fichiers in os.walk(dossier):
        sous_dossiers[:] = sorted(d for d in sous_dossiers if d not in DOSSIERS_IGNORES)
        for nom in sorted(fichiers):
            chemin = Path(courant) / nom
            if chemin.suffix.lower() in EXTENSIONS_XML and not chemin.is_symlink():
                trouves.append(chemin)
                if len(trouves) >= plafond:
                    return trouves, True
    return trouves, False


def rassembler_sources(entrees: list[str], texte: str | None, base: Path,
                       plafond: int) -> tuple[list[Source], bool]:
    """Transforme les arguments en sources ; lève EntreeInvalide sur un chemin absent."""
    sources = [Source("<texte>", texte=texte)] if texte is not None else []
    tronque = False
    for entree in entrees:
        if entree == "-":
            sources.append(Source("<stdin>"))
            continue
        chemin = Path(entree) if Path(entree).is_absolute() else base / entree
        if chemin.is_dir():
            fichiers, coupe = lister_fichiers(chemin, plafond)
            tronque = tronque or coupe
            sources += [Source(str(f.relative_to(chemin)), chemin=f) for f in fichiers]
        elif chemin.is_file():
            sources.append(Source(entree, chemin=chemin))
        else:
            raise EntreeInvalide(f"chemin introuvable ou non ordinaire : {entree}")
    return sources, tronque


def analyser_tout(sources: list[Source], args: argparse.Namespace) -> list[dict[str, Any]]:
    """Analyse chaque source ; une source illisible arrête tout (code 2)."""
    plafonds = Plafonds(args.max_profondeur, args.max_substitutions, args.max_taille, args.strict)
    resultats = []
    for source in sources:
        try:
            resultat = analyser_source(source, plafonds)
            resultat["defusedxml"] = comparer_defusedxml(source, args.max_octets_conversion)
            if args.convertir and resultat["verdict"] == "sur":
                resultat["conversion"] = conversion_bornee(source, resultat, args.max_octets_conversion)
        except OSError as exc:
            raise EntreeInvalide(f"lecture impossible de {source.nom} : {exc.strerror or exc}") from exc
        except ValueError as exc:
            raise EntreeInvalide(f"texte inutilisable pour {source.nom} : {exc}") from exc
        resultats.append(resultat)
    return resultats


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


def construire_rapport(resultats: list[dict[str, Any]], tronque: bool,
                       args: argparse.Namespace) -> dict[str, Any]:
    """Objet JSON unique : dénominateur, examinés, verdicts, moteurs, contrat."""
    verdicts = Counter(r["verdict"] for r in resultats)
    noms = [r["chemin"] for r in resultats]
    return {
        "outil": NOM_OUTIL,
        "moteur": "stdlib",
        "moteur_conversion": (("xmltodict" if xmltodict else "stdlib") if args.convertir else None),
        "comparaison": f"defusedxml {defusedxml.__version__}" if defusedxml else None,
        "denominateur": len(resultats),
        "examines": noms[:PLAFOND_EXAMINES],
        "examines_tronques": len(noms) > PLAFOND_EXAMINES or tronque,
        "dangereux": verdicts["dangereux"],
        "mal_formes": verdicts["mal_forme"],
        "surs": verdicts["sur"],
        "plafonds": {"profondeur": args.max_profondeur, "substitutions": args.max_substitutions,
                     "taille": args.max_taille, "strict": args.strict},
        "resultats": resultats,
        "contrat": lire_contrat(),
    }


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Une ligne par document, puis le bilan."""
    for r in rapport["resultats"]:
        print(f"{r['chemin']} : {r['verdict'].upper()}")
        for danger in r["dangers"]:
            print(f"  danger {danger['type']} : {danger['detail']}")
        for avert in r["avertissements"]:
            print(f"  avertissement {avert['type']} : {avert['detail']}")
        if r["erreur"]:
            e = r["erreur"]
            print(f"  erreur ligne {e['ligne']} colonne {e['colonne']} : {e['message']}")
        afficher_structure(r["structure"])
        if r.get("conversion", {}).get("converti"):
            print(json.dumps(r["conversion"]["valeur"], ensure_ascii=False, indent=2))
    print(f"{rapport['denominateur']} document(s) : {rapport['dangereux']} dangereux, "
          f"{rapport['mal_formes']} mal formé(s), {rapport['surs']} sûr(s)")


def afficher_structure(structure: dict[str, Any]) -> None:
    """Résumé de la structure sur une ligne."""
    racine = structure["racine"] or {}
    print(f"  racine {racine.get('nom')} ; {structure['elements']} élément(s), "
          f"{structure['elements_distincts']} distinct(s), profondeur {structure['profondeur_max']}, "
          f"espaces de noms {structure['espaces_de_noms'] or 'aucun'}")


def afficher_json(rapport: dict[str, Any]) -> None:
    print(json.dumps(rapport, ensure_ascii=False, indent=2))


def signaler_bibliotheques() -> None:
    """Une seule ligne sur stderr quand une bibliothèque facultative manque."""
    manques = []
    if defusedxml is None:
        manques.append("defusedxml absent : pas de comparaison des refus")
    if xmltodict is None:
        manques.append("xmltodict absent : conversion JSON par le repli stdlib (même convention)")
    if manques:
        print("mode dégradé — " + " ; ".join(manques), file=sys.stderr)


def construire_parseur() -> argparse.ArgumentParser:
    parseur = argparse.ArgumentParser(
        description="Dit si un XML peut être lu sans danger (entités externes, bombes "
                    "d'entités) sans jamais rien résoudre, puis décrit sa structure.",
        epilog="Exemple : python lire_xml_sur.py config.xml --json\n"
               "          python lire_xml_sur.py --texte '<a><b/></a>' --convertir --json\n"
               "Codes : 0 sûr, 1 dangereux ou mal formé, 2 entrée invalide, 3 rien à examiner.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parseur.add_argument("chemins", nargs="*",
                         help="fichiers XML ou dossiers (fichiers aux extensions XML) ; - = entrée standard")
    parseur.add_argument("--texte", help="document XML donné en ligne")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="dossier de base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--strict", action="store_true",
                         help="DTD externe et entités internes deviennent des dangers (comme defusedxml)")
    parseur.add_argument("--max-profondeur", type=int, default=5, help="imbrication d'entités tolérée")
    parseur.add_argument("--max-substitutions", type=int, default=10_000,
                         help="substitutions d'entités tolérées dans le document")
    parseur.add_argument("--max-taille", type=int, default=1_000_000,
                         help="caractères déployés tolérés")
    parseur.add_argument("--convertir", action="store_true", help="ajoute la conversion JSON des documents sûrs")
    parseur.add_argument("--max-octets-conversion", type=int, default=16 << 20,
                         help="taille maximale d'un fichier converti ou comparé (octets)")
    parseur.add_argument("--max-fichiers", type=int, default=2000, help="fichiers lus au plus par dossier")
    return parseur


def base_relative(racine: Path | None) -> Path:
    """Base des chemins relatifs : --racine, sinon le dossier courant (sinon celui de l'outil)."""
    if racine is not None:
        return racine
    try:
        return Path.cwd()
    except OSError:
        return RACINE


def main() -> int:
    args = construire_parseur().parse_args()
    if not args.chemins and args.texte is None:
        print("entrée invalide : donner un chemin, - ou --texte", file=sys.stderr)
        return 2
    if min(args.max_profondeur, args.max_substitutions, args.max_taille, args.max_fichiers) < 1:
        print("entrée invalide : les plafonds doivent être positifs", file=sys.stderr)
        return 2
    signaler_bibliotheques()
    try:
        sources, tronque = rassembler_sources(args.chemins, args.texte, base_relative(args.racine),
                                              args.max_fichiers)
        resultats = analyser_tout(sources, args)
    except EntreeInvalide as exc:
        print(f"entrée invalide : {exc}", file=sys.stderr)
        return 2
    rapport = construire_rapport(resultats, tronque, args)
    if args.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    if not resultats:
        print("dénominateur nul : aucun fichier XML trouvé, rien à examiner", file=sys.stderr)
        return 3
    return 1 if rapport["dangereux"] or rapport["mal_formes"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
