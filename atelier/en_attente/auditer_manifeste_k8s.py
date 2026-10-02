"""Audite des manifestes Kubernetes (YAML ou JSON) contre les règles courantes de sécurité et de fiabilité.

Pourquoi : sur l'interpréteur de référence, ``import yaml`` échoue (ModuleNotFoundError), donc un
agent ne peut même pas lire un manifeste sans dépendance. Mesuré le 2026-10-02 sur le Deployment
« nginx-deployment » du tutoriel Kubernetes (21 lignes, 1 conteneur, image nginx:1.14.2) : cet
outil y relève 7 constats (3 de gravité moyenne, 4 basse, aucun haut), identiques avec les deux
moteurs ; son analyseur de repli rend le même résultat que PyYAML sur 2021 des 2273 fichiers
YAML lisibles par PyYAML sur cette machine, et refuse explicitement les 252 autres.

QUESTION
    Ces manifestes Kubernetes respectent-ils les bonnes pratiques de sécurité et de fiabilité ?
MESURE
    Pour chaque objet (documents multiples, kind List, fichiers ou dossiers, ou --texte) dont
    le gabarit de pod est connu (Pod, Deployment, ReplicaSet, StatefulSet, DaemonSet, Job,
    CronJob, ReplicationController, PodTemplate) : limites et requests cpu/mémoire, image
    « latest » ou sans étiquette ni empreinte, privileged, allowPrivilegeEscalation non fixé à
    false, runAsNonRoot absent ou runAsUser 0 (conteneur puis pod), readOnlyRootFilesystem,
    capacités dangereuses ajoutées, hostPath, hostNetwork, hostPID, hostIPC, sondes liveness et
    readiness absentes (charges longues seulement), secrets en clair dans env (nom évocateur ou
    forme de jeton connue ; la valeur n'est jamais recopiée). Un Secret versionné est noté
    « info ». Lecture YAML par PyYAML (SafeLoader) s'il est installé, sinon par un analyseur
    stdlib d'un sous-ensemble documenté ; JSON par le module json.
HYPOTHÈSES
    Les manifestes sont rendus (pas de gabarit Helm « {{ }} » : ces fichiers sont écartés et
    signalés). Les valeurs par défaut supposées sont celles de Kubernetes sans contrôleur
    d'admission (Pod Security Admission, Kyverno, Gatekeeper) qui les modifierait.
LIMITES
    Sous-ensemble YAML du repli stdlib : documents « --- », mappings et séquences en bloc (y
    compris séquences non indentées), scalaires simples, entre apostrophes ou guillemets sur
    une ligne, scalaires de bloc | et > (indicateurs de coupe et d'indentation), collections
    en flux [..] {..} tenant sur une ligne, commentaires. Refus explicite (code 2) au-delà :
    ancres, alias, étiquettes, clés complexes, directives, flux ou chaînes sur plusieurs
    lignes, tabulations d'indentation. Les dates restent des chaînes. Aucune connaissance des
    CRD : un objet inconnu est compté, pas audité. Pas de lecture de LimitRange ni de quotas
    qui fourniraient des valeurs par défaut.
CONTRE-EXEMPLES
    Constaté : un conteneur sur l'image gcr.io/distroless/static:nonroot, conçue pour tourner
    sans root, est signalé « runAsNonRoot absent » faute de champ dans le manifeste : l'outil ne
    lit pas l'image. Constaté : un mot de passe en clair dans une variable nommée
    db_pass_file_content (valeur « hunter2 », casse indifférente) n'est pas signalé : ni le nom
    ni la valeur n'ont une forme reconnue.
INVOCATION
    {outil} --texte '{"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "demo"}, "spec": {"containers": [{"name": "app", "image": "nginx:latest"}]}}' --json
DOMAINE
    Revue de manifestes avant kubectl apply, en CI ou en revue de code ; manifestes rendus
    (kustomize build, helm template).
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Sequence

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

try:
    import yaml
except ImportError:
    yaml = None

RACINE = Path(__file__).resolve().parent

INTITULE_HYPOTHESES = "HYPOTHÈSES"
INTITULE_INVOCATION = "INVOCATION"
INTITULES = ("QUESTION", "MESURE", INTITULE_HYPOTHESES, "LIMITES", "CONTRE-EXEMPLES",
             INTITULE_INVOCATION, "DOMAINE")

FORMAT_YAML = "YAML"
MAX_EXAMINES = 200
MAX_OCTETS = 20 * 1024 * 1024
EXTENSIONS = frozenset({".yaml", ".yml", ".json"})
GRAVITES = ("info", "basse", "moyenne", "haute")

# Chemins du gabarit de pod selon le kind, et charges « longues » (sondes attendues).
GABARITS = MappingProxyType({
    "Pod": ("spec",), "PodTemplate": ("template", "spec"),
    "Deployment": ("spec", "template", "spec"), "ReplicaSet": ("spec", "template", "spec"),
    "StatefulSet": ("spec", "template", "spec"), "DaemonSet": ("spec", "template", "spec"),
    "ReplicationController": ("spec", "template", "spec"), "Job": ("spec", "template", "spec"),
    "CronJob": ("spec", "jobTemplate", "spec", "template", "spec"),
})
CHARGES_LONGUES = frozenset({"Pod", "Deployment", "ReplicaSet", "StatefulSet", "DaemonSet",
                             "ReplicationController"})
CAPACITES_DANGEREUSES = frozenset({"ALL", "SYS_ADMIN", "NET_ADMIN", "SYS_PTRACE", "SYS_MODULE",
                                   "DAC_READ_SEARCH", "SYS_RAWIO", "BPF", "PERFMON"})
JETONS_SECRETS = frozenset({"PASSWORD", "PASSWD", "PWD", "SECRET", "TOKEN", "APIKEY",
                            "CREDENTIAL", "CREDENTIALS", "PASSPHRASE", "PRIVATEKEY"})
PAIRES_SECRETES = (("API", "KEY"), ("PRIVATE", "KEY"), ("ACCESS", "KEY"), ("SECRET", "KEY"),
                   ("CLIENT", "SECRET"))
SUFFIXES_INNOCENTS = frozenset({"URL", "URI", "ENDPOINT", "FILE", "PATH", "DIR", "HOST", "PORT",
                                "NAME", "USER", "USERNAME", "ID", "TTL", "EXPIRY", "LENGTH",
                                "TIMEOUT", "HEADER", "TYPE", "ENABLED", "REF", "KEY_ID", "REGION"})
FORMES_JETONS = (
    ("clé d'accès AWS", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("jeton GitHub", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("jeton Slack", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("clé privée PEM", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("clé d'API Google", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("clé Stripe", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{20,}")),
    ("mot de passe dans une URL", re.compile(r"[a-z][a-z0-9+.-]*://[^:/\s@]+:[^@/\s]+@")),
)

# Résolution implicite des scalaires simples, copiée de PyYAML (YAML 1.1, SafeLoader).
RE_NUL = re.compile(r"^(?:~|null|Null|NULL|)$")
RE_BOOL = re.compile(r"^(?:yes|Yes|YES|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF)$")
RE_ENTIER = re.compile(r"""^(?:[-+]?0b[0-1_]+|[-+]?0[0-7_]+|[-+]?(?:0|[1-9][0-9_]*)
                           |[-+]?0x[0-9a-fA-F_]+|[-+]?[1-9][0-9_]*(?::[0-5]?[0-9])+)$""", re.X)
RE_REEL = re.compile(r"""^(?:[-+]?(?:[0-9][0-9_]*)\.[0-9_]*(?:[eE][-+][0-9]+)?
                         |\.[0-9_]+(?:[eE][-+][0-9]+)?
                         |[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+\.[0-9_]*
                         |[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN))$""", re.X)
ECHAPPEMENTS = MappingProxyType({"0": "\0", "a": "\a", "b": "\b", "t": "\t", "\t": "\t", "n": "\n",
                                 "v": "\v", "f": "\f", "r": "\r", "e": "\x1b", " ": " ", '"': '"',
                                 "/": "/", "\\": "\\", "N": "\x85", "_": "\xa0", "L": " ",
                                 "P": " "})
LONGUEURS_HEX = MappingProxyType({"x": 2, "u": 4, "U": 8})


class EntreeInvalide(Exception):
    """Entrée illisible, binaire, ou YAML hors du sous-ensemble pris en charge (code 2)."""


@dataclass
class Constat:
    """Un manquement relevé sur un objet."""

    fichier: str
    document: int
    objet: str
    regle: str
    gravite: str
    chemin: str
    message: str
    conteneur: str = ""


@dataclass
class Bilan:
    """Accumulateur : objets examinés, constats, fichiers écartés ou refusés."""

    objets: list[str] = field(default_factory=list)
    conteneurs: int = 0
    constats: list[Constat] = field(default_factory=list)
    ecartes: list[dict[str, str]] = field(default_factory=list)
    invalides: list[dict[str, str]] = field(default_factory=list)
    fichiers: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- contrat


def extraire_contrat(doc: str) -> dict[str, str]:
    """Découpe la docstring du module en sections selon les intitulés du socle."""
    contrat: dict[str, str] = {}
    courant = "POURQUOI"
    lignes: list[str] = []
    for ligne in doc.splitlines():
        if ligne.strip() in INTITULES and not ligne.startswith(" "):
            contrat[courant] = " ".join(x.strip() for x in lignes if x.strip())
            courant, lignes = ligne.strip(), []
        else:
            lignes.append(ligne)
    contrat[courant] = " ".join(x.strip() for x in lignes if x.strip())
    return contrat


# --------------------------------------------------------------------------- YAML : scalaires


def resoudre_scalaire(texte: str) -> Any:
    """Type implicite d'un scalaire simple, comme le SafeLoader de PyYAML (dates exceptées)."""
    if RE_NUL.match(texte):
        return None
    if RE_BOOL.match(texte):
        return texte.lower() in ("yes", "true", "on")
    if RE_ENTIER.match(texte):
        return construire_entier(texte)
    if RE_REEL.match(texte):
        return construire_reel(texte)
    return texte


def construire_entier(texte: str) -> int:
    """int YAML 1.1 : binaire, octal (0 initial), hexadécimal, base 60, soulignés."""
    valeur = texte.replace("_", "")
    signe = -1 if valeur.startswith("-") else 1
    valeur = valeur.lstrip("+-")
    if valeur.startswith("0b"):
        return signe * int(valeur[2:], 2)
    if valeur.startswith("0x"):
        return signe * int(valeur[2:], 16)
    if valeur != "0" and valeur.startswith("0"):
        return signe * int(valeur, 8)
    if ":" in valeur:
        return signe * base_soixante(valeur, int)
    return signe * int(valeur)


def construire_reel(texte: str) -> float:
    """float YAML 1.1 : infinis, NaN, base 60, soulignés."""
    valeur = texte.replace("_", "").lower()
    signe = -1.0 if valeur.startswith("-") else 1.0
    valeur = valeur.lstrip("+-")
    if valeur == ".inf":
        return signe * float("inf")
    if valeur == ".nan":
        return float("nan")
    if ":" in valeur:
        return signe * base_soixante(valeur, float)
    return signe * float(valeur)


def base_soixante(valeur: str, conversion: type) -> Any:
    """Notation sexagésimale de YAML 1.1 (190:20:30)."""
    total = conversion(0)
    for chiffre in valeur.split(":"):
        total = total * 60 + conversion(chiffre)
    return total


def lire_guillemets(texte: str, i: int, numero: int) -> tuple[str, int]:
    """Chaîne entre guillemets doubles sur une ligne ; rend (valeur, index après)."""
    morceaux, i = [], i + 1
    while i < len(texte):
        c = texte[i]
        if c == '"':
            return "".join(morceaux), i + 1
        if c != "\\":
            morceaux.append(c)
            i += 1
            continue
        code = texte[i + 1:i + 2]
        if code in ECHAPPEMENTS:
            morceaux.append(ECHAPPEMENTS[code])
            i += 2
        elif code in LONGUEURS_HEX:
            n = LONGUEURS_HEX[code]
            try:
                morceaux.append(chr(int(texte[i + 2:i + 2 + n], 16)))
            except ValueError as exc:
                raise refus(numero, f"échappement \\{code} invalide") from exc
            i += 2 + n
        else:
            raise refus(numero, "échappement inconnu ou chaîne sur plusieurs lignes")
    raise refus(numero, "chaîne entre guillemets sur plusieurs lignes")


def lire_apostrophes(texte: str, i: int, numero: int) -> tuple[str, int]:
    """Chaîne entre apostrophes sur une ligne ('' vaut une apostrophe)."""
    morceaux, i = [], i + 1
    while i < len(texte):
        if texte[i] == "'":
            if texte[i + 1:i + 2] == "'":
                morceaux.append("'")
                i += 2
                continue
            return "".join(morceaux), i + 1
        morceaux.append(texte[i])
        i += 1
    raise refus(numero, "chaîne entre apostrophes sur plusieurs lignes")


def refus(numero: int, raison: str) -> EntreeInvalide:
    """Erreur de sous-ensemble YAML, avec le numéro de ligne."""
    return EntreeInvalide(f"ligne {numero} : {raison} (hors du sous-ensemble YAML du repli "
                          "stdlib ; installer PyYAML pour ce fichier)")


# --------------------------------------------------------------------------- YAML : flux


def lire_flux(texte: str, i: int, numero: int) -> tuple[Any, int]:
    """Valeur en flux ([..], {..}, chaîne, scalaire) tenant sur une ligne."""
    i = sauter_blancs(texte, i)
    if i >= len(texte):
        raise refus(numero, "collection en flux sur plusieurs lignes")
    c = texte[i]
    if c == "[":
        return lire_sequence_flux(texte, i, numero)
    if c == "{":
        return lire_mapping_flux(texte, i, numero)
    if c == '"':
        return lire_guillemets(texte, i, numero)
    if c == "'":
        return lire_apostrophes(texte, i, numero)
    if c in "&*!":
        raise refus(numero, "ancre, alias ou étiquette")
    fin = i
    while fin < len(texte) and texte[fin] not in ",]}" and not (
            texte[fin] == ":" and (fin + 1 == len(texte) or texte[fin + 1] in " ,]}")):
        fin += 1
    return resoudre_scalaire(texte[i:fin].strip()), fin


def sauter_blancs(texte: str, i: int) -> int:
    """Avance au-delà des espaces."""
    while i < len(texte) and texte[i] == " ":
        i += 1
    return i


def lire_sequence_flux(texte: str, i: int, numero: int) -> tuple[list[Any], int]:
    """[a, b, ...] sur une ligne."""
    valeurs: list[Any] = []
    i = sauter_blancs(texte, i + 1)
    while i < len(texte) and texte[i] != "]":
        valeur, i = lire_flux(texte, i, numero)
        i = sauter_blancs(texte, i)
        if texte[i:i + 1] == ":":
            raise refus(numero, "paire clé: valeur dans une séquence en flux")
        valeurs.append(valeur)
        if texte[i:i + 1] == ",":
            i = sauter_blancs(texte, i + 1)
    if i >= len(texte):
        raise refus(numero, "collection en flux sur plusieurs lignes")
    return valeurs, i + 1


def lire_mapping_flux(texte: str, i: int, numero: int) -> tuple[dict[Any, Any], int]:
    """{a: 1, b: [x]} sur une ligne."""
    resultat: dict[Any, Any] = {}
    i = sauter_blancs(texte, i + 1)
    while i < len(texte) and texte[i] != "}":
        cle, i = lire_flux(texte, i, numero)
        i = sauter_blancs(texte, i)
        valeur: Any = None
        if texte[i:i + 1] == ":":
            i = sauter_blancs(texte, i + 1)
            if texte[i:i + 1] not in (",", "}"):
                valeur, i = lire_flux(texte, i, numero)
                i = sauter_blancs(texte, i)
        resultat[cle] = valeur
        if texte[i:i + 1] == ",":
            i = sauter_blancs(texte, i + 1)
    if i >= len(texte):
        raise refus(numero, "collection en flux sur plusieurs lignes")
    return resultat, i + 1


# --------------------------------------------------------------------------- YAML : blocs


def sans_commentaire(ligne: str) -> str:
    """Retire un commentaire « # » précédé d'un blanc, hors chaînes ; rogne la fin."""
    guillemet = ""
    for i, c in enumerate(ligne):
        if guillemet:
            if c == guillemet and not (c == '"' and ligne[i - 1] == "\\"):
                guillemet = ""
        elif c in "\"'" and (i == 0 or ligne[i - 1] in " [{,:-"):
            guillemet = c
        elif c == "#" and (i == 0 or ligne[i - 1] in " \t"):
            return ligne[:i].rstrip()
    return ligne.rstrip()


@dataclass
class Analyseur:
    """Analyseur descendant du sous-ensemble YAML, ligne par ligne, pour un document."""

    lignes: list[str]
    premier: int
    sans_saut_final: bool = False
    position: int = 0

    def numero(self, index: int) -> int:
        """Numéro de ligne dans le fichier."""
        return self.premier + index

    def suivante(self) -> tuple[int, int, str] | None:
        """(index, indentation, contenu) de la prochaine ligne utile, sans la consommer."""
        i = self.position
        while i < len(self.lignes):
            brute = self.lignes[i]
            contenu = sans_commentaire(brute)
            if contenu.strip():
                marge = len(brute) - len(brute.lstrip(" "))
                if "\t" in brute[:len(brute) - len(brute.lstrip())]:
                    raise refus(self.numero(i), "tabulation dans l'indentation")
                return i, marge, contenu.strip()
            i += 1
        return None

    def noeud(self, minimum: int) -> Any:
        """Nœud de bloc dont la première ligne est indentée d'au moins `minimum`."""
        tete = self.suivante()
        if tete is None or tete[1] < minimum:
            return None
        _, marge, contenu = tete
        if est_tiret(contenu):
            return self.sequence(marge)
        if separer_cle(contenu) is not None:
            return self.mapping(marge)
        self.position = tete[0] + 1
        valeur = self.valeur(contenu, marge - 1, tete[0])
        self.verifier_fin_scalaire(marge)
        return valeur

    def sequence(self, marge: int) -> list[Any]:
        """Séquence de bloc : lignes « - » à l'indentation `marge`."""
        valeurs: list[Any] = []
        while (tete := self.suivante()) is not None and tete[1] == marge and est_tiret(tete[2]):
            index, _, contenu = tete
            self.position = index + 1
            reste = contenu[1:].lstrip(" ")
            colonne = marge + len(contenu) - len(reste)
            if not reste:
                valeurs.append(self.noeud(marge + 1))
            elif est_tiret(reste):
                raise refus(self.numero(index), "séquence imbriquée sur une même ligne (« - - »)")
            elif separer_cle(reste) is not None:
                valeurs.append(self.mapping(colonne, (index, reste)))
            else:
                valeurs.append(self.valeur(reste, marge, index))
                self.verifier_fin_scalaire(marge + 1)
        self.verifier_desindentation(marge)
        return valeurs

    def mapping(self, marge: int, premiere: tuple[int, str] | None = None) -> dict[Any, Any]:
        """Mapping de bloc : entrées « clé: valeur » à l'indentation `marge`."""
        resultat: dict[Any, Any] = {}
        while True:
            if premiere is not None:
                index, contenu = premiere
                premiere = None
            else:
                tete = self.suivante()
                if tete is None or tete[1] != marge or est_tiret(tete[2]):
                    break
                index, _, contenu = tete
            self.position = index + 1
            cle, reste = self.cle_et_reste(contenu, index)
            resultat[cle] = self.valeur_de_cle(reste, marge, index)
        self.verifier_desindentation(marge)
        return resultat

    def cle_et_reste(self, contenu: str, index: int) -> tuple[Any, str]:
        """Sépare la clé (résolue comme un scalaire) du reste de la ligne."""
        decoupe = separer_cle(contenu)
        if decoupe is None:
            raise refus(self.numero(index), "entrée de mapping attendue (« clé: valeur »)")
        texte_cle, reste = decoupe
        if texte_cle.startswith(("&", "*", "!", "?")) or texte_cle == "<<":
            raise refus(self.numero(index), "ancre, alias, étiquette, clé complexe ou fusion")
        if texte_cle[:1] in "\"'":
            lecteur = lire_guillemets if texte_cle[0] == '"' else lire_apostrophes
            return lecteur(texte_cle, 0, self.numero(index))[0], reste
        return resoudre_scalaire(texte_cle), reste

    def valeur_de_cle(self, reste: str, marge: int, index: int) -> Any:
        """Valeur d'une clé : sur la ligne, ou bloc plus indenté, ou séquence non indentée."""
        if reste:
            valeur = self.valeur(reste, marge, index)
            if not reste.startswith(("|", ">")):
                self.verifier_fin_scalaire(marge + 1)
            return valeur
        tete = self.suivante()
        if tete is not None and tete[1] == marge and est_tiret(tete[2]):
            return self.sequence(marge)
        return self.noeud(marge + 1)

    def valeur(self, texte: str, parent: int, index: int) -> Any:
        """Valeur écrite sur la ligne : bloc | >, flux, chaîne, scalaire simple."""
        numero = self.numero(index)
        if texte[0] in "&*!%@`":
            raise refus(numero, "ancre, alias, étiquette ou indicateur réservé")
        if texte[0] in "|>":
            return self.scalaire_bloc(texte, parent, index)
        if texte[0] in "[{\"'":
            valeur, fin = lire_flux(texte, 0, numero)
            if texte[fin:].strip():
                raise refus(numero, "texte inattendu après une valeur")
            return valeur
        return resoudre_scalaire(texte)

    def verifier_fin_scalaire(self, minimum: int) -> None:
        """Une ligne plus indentée après un scalaire serait une continuation : refusée."""
        tete = self.suivante()
        if tete is not None and tete[1] >= minimum:
            raise refus(self.numero(tete[0]), "scalaire sur plusieurs lignes ou indentation inattendue")

    def verifier_desindentation(self, marge: int) -> None:
        """Après une collection, la ligne suivante doit être moins indentée."""
        tete = self.suivante()
        if tete is not None and tete[1] > marge:
            raise refus(self.numero(tete[0]), "indentation incohérente")

    def scalaire_bloc(self, entete: str, parent: int, index: int) -> str:
        """Scalaire de bloc littéral (|) ou replié (>), coupe - + et indentation explicite."""
        indicateurs = entete[1:].strip()
        if not re.fullmatch(r"[-+]?[1-9]?|[1-9][-+]", indicateurs):
            raise refus(self.numero(index), f"en-tête de bloc « {entete} » non pris en charge")
        coupe = "+" if "+" in indicateurs else ("-" if "-" in indicateurs else "")
        chiffre = re.search(r"[1-9]", indicateurs)
        lignes, i = self.lignes_de_bloc(parent, int(chiffre.group()) if chiffre else 0, index + 1)
        self.position = i
        corps = "\n".join(lignes).rstrip("\n") if entete[0] == "|" else replier(lignes)
        finales = len(lignes) - len("\n".join(lignes).rstrip("\n").split("\n")) if lignes else 0
        sauts = 1 + finales - (1 if self.sans_saut_final and i >= len(self.lignes) else 0)
        if not corps.strip("\n") and coupe != "+":
            return ""
        if coupe == "-":
            return corps
        return corps + "\n" * (sauts if coupe == "+" else min(1, sauts))

    def lignes_de_bloc(self, parent: int, explicite: int, debut: int) -> tuple[list[str], int]:
        """Lignes d'un scalaire de bloc, débarrassées de l'indentation de contenu."""
        marge = parent + explicite if explicite else 0
        i, lignes = debut, []
        while i < len(self.lignes):
            brute = self.lignes[i]
            if brute.strip():
                retrait = len(brute) - len(brute.lstrip(" "))
                marge = marge or retrait
                if retrait < marge or retrait <= parent:
                    break
            lignes.append(brute[marge:] if marge else "")
            i += 1
        return lignes, i


def est_tiret(contenu: str) -> bool:
    """Ligne d'entrée de séquence de bloc : « - » seul ou suivi d'un blanc."""
    return contenu == "-" or contenu.startswith("- ")


def separer_cle(contenu: str) -> tuple[str, str] | None:
    """« clé: reste » si la ligne est une entrée de mapping, sinon None."""
    if contenu[:1] in "\"'":
        lecteur = lire_guillemets if contenu[0] == '"' else lire_apostrophes
        try:
            _, fin = lecteur(contenu, 0, 0)
        except EntreeInvalide:
            return None
        if contenu[fin:fin + 1] == ":" and (fin + 1 == len(contenu) or contenu[fin + 1] == " "):
            return contenu[:fin], contenu[fin + 1:].strip()
        return None
    if contenu[:1] in "[{|>":
        return None
    correspondance = re.search(r":(?: |$)", contenu)
    if correspondance is None:
        return None
    return contenu[:correspondance.start()].strip(), contenu[correspondance.end():].strip()


def replier(lignes: list[str]) -> str:
    """Repliement d'un scalaire « > » : sauts simples en espaces, lignes vides et plus indentées gardées."""
    debut = 0
    while debut < len(lignes) and lignes[debut] == "":
        debut += 1
    utiles = lignes[debut:]
    while utiles and utiles[-1] == "":
        utiles.pop()
    if not utiles:
        return "\n" * debut
    resultat, i = "\n" * debut + utiles[0], 1
    while i < len(utiles):
        j = i
        while j < len(utiles) and utiles[j] == "":
            j += 1
        vides = j - i
        precedente, suivante = utiles[i - 1], utiles[j]
        indentee = precedente.startswith((" ", "\t")) or suivante.startswith((" ", "\t"))
        if vides == 0:
            resultat += ("\n" if indentee else " ") + suivante
        else:
            resultat += "\n" * (vides + (1 if indentee else 0)) + suivante
        i = j + 1
    return resultat


def decouper_documents(texte: str) -> Iterator[tuple[int, list[str]]]:
    """Documents séparés par « --- » ; rend (numéro de la première ligne, lignes)."""
    lignes = texte.split("\n")
    if lignes and lignes[-1] == "":
        lignes.pop()
    debut, courant = 1, []
    for numero, ligne in enumerate(lignes, start=1):
        if ligne.startswith("%"):
            raise refus(numero, "directive YAML")
        if re.match(r"^---(?:\s|$)", ligne):
            if sans_commentaire(ligne[3:]).strip():
                raise refus(numero, "contenu sur la ligne « --- »")
            yield debut, courant
            debut, courant = numero + 1, []
        elif re.match(r"^\.\.\.\s*$", ligne):
            yield debut, courant
            debut, courant = numero + 1, []
        else:
            courant.append(ligne)
    yield debut, courant


def charger_yaml_stdlib(texte: str) -> list[Any]:
    """Tous les documents non vides, via l'analyseur du sous-ensemble."""
    documents = []
    total = texte.count("\n") + (0 if texte.endswith("\n") else 1)
    for premier, lignes in decouper_documents(texte):
        if not any(sans_commentaire(l).strip() for l in lignes):
            continue
        dernier = premier + len(lignes) - 1 == total and not texte.endswith("\n")
        analyseur = Analyseur(lignes, premier, dernier)
        valeur = analyseur.noeud(0)
        reste = analyseur.suivante()
        if reste is not None:
            raise refus(analyseur.numero(reste[0]), "contenu inattendu après le nœud racine")
        documents.append(valeur)
    return documents


def charger_yaml(texte: str, moteur: str) -> list[Any]:
    """Documents YAML par PyYAML (SafeLoader) ou par le repli stdlib."""
    if moteur == "stdlib":
        return charger_yaml_stdlib(texte)
    chargeur = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
    try:
        return [d for d in yaml.load_all(texte, Loader=chargeur) if d is not None]
    except yaml.YAMLError as exc:
        raise EntreeInvalide(f"YAML invalide : {str(exc).splitlines()[0]}") from exc


# --------------------------------------------------------------------------- lecture des fichiers


def decoder(donnees: bytes) -> str:
    """Texte d'un manifeste : refuse binaire et encodages autres qu'utf-8/utf-16 à BOM."""
    if donnees.startswith((b"\xff\xfe", b"\xfe\xff")):
        return donnees.decode("utf-16")
    if b"\x00" in donnees[:8192]:
        raise EntreeInvalide("fichier binaire (octet nul), pas un manifeste")
    try:
        return donnees.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError as exc:
        raise EntreeInvalide(f"n'est pas de l'utf-8 ({exc.reason} à l'octet {exc.start})") from exc


def charger_texte(texte: str, nom: str, moteur: str) -> list[Any]:
    """Documents d'un texte : JSON si le texte en est, sinon YAML."""
    debut = texte.lstrip()[:1]
    if nom.endswith(".json") or debut in "{[" and debut:
        try:
            valeur = json.loads(texte)
            return [valeur] if not isinstance(valeur, list) else valeur
        except json.JSONDecodeError as exc:
            if nom.endswith(".json"):
                raise EntreeInvalide(f"JSON invalide ({exc.msg}, ligne {exc.lineno})") from exc
    return charger_yaml(texte, moteur)


def collecter(cibles: Sequence[str], base: Path, bilan: Bilan) -> list[tuple[Path, str, bool]]:
    """(chemin, affichage, explicite) des fichiers à lire ; consigne les chemins invalides."""
    fichiers: list[tuple[Path, str, bool]] = []
    for texte in cibles:
        chemin = Path(texte) if Path(texte).is_absolute() else base / texte
        if not chemin.exists():
            bilan.invalides.append({"fichier": texte, "erreur": "chemin inexistant"})
        elif chemin.is_dir():
            for f in sorted(chemin.rglob("*")):
                if f.suffix.lower() in EXTENSIONS and f.is_file() and ".git" not in f.parts:
                    fichiers.append((f, str(Path(texte) / f.relative_to(chemin)), False))
        elif chemin.is_file():
            fichiers.append((chemin, texte, True))
        else:
            bilan.invalides.append({"fichier": texte, "erreur": "ni fichier régulier ni dossier"})
    return fichiers


def lire_manifeste(chemin: Path, nom: str, explicite: bool, moteur: str, bilan: Bilan) -> list[Any] | None:
    """Documents d'un fichier, ou None s'il est écarté (non Kubernetes, gabarit Helm)."""
    if chemin.stat().st_size > MAX_OCTETS:
        raise EntreeInvalide(f"plus de {MAX_OCTETS} octets, refusé")
    texte = decoder(chemin.read_bytes())
    if "{{" in texte and "}}" in texte:
        bilan.ecartes.append({"fichier": nom, "raison": "gabarit non rendu (« {{ }} ») : rendre d'abord"})
        return None
    if not explicite and not re.search(r"(?:^|[\s{,])[\"']?kind[\"']?\s*:", texte):
        bilan.ecartes.append({"fichier": nom, "raison": "aucune clé kind : pas un manifeste Kubernetes"})
        return None
    return charger_texte(texte, nom, moteur)


# --------------------------------------------------------------------------- audit


def objets_kubernetes(documents: Sequence[Any]) -> Iterator[tuple[int, dict[str, Any]]]:
    """(numéro de document, objet) ; les kind: List sont dépliés."""
    for numero, document in enumerate(documents, start=1):
        if not isinstance(document, dict):
            continue
        if document.get("kind") == "List" and isinstance(document.get("items"), list):
            for item in document["items"]:
                if isinstance(item, dict) and "kind" in item:
                    yield numero, item
        elif "kind" in document and "apiVersion" in document:
            yield numero, document


def descendre(objet: Any, chemin: Sequence[str]) -> Any:
    """Valeur au bout d'un chemin de clés, ou None."""
    for cle in chemin:
        if not isinstance(objet, dict):
            return None
        objet = objet.get(cle)
    return objet


def dictionnaire(valeur: Any) -> dict[str, Any]:
    """La valeur si c'est un dict, sinon un dict vide (champs absents ou mal typés)."""
    return valeur if isinstance(valeur, dict) else {}


def auditer_objet(fichier: str, document: int, objet: dict[str, Any], bilan: Bilan) -> None:
    """Audite un objet : gabarit de pod s'il en a un, Secret versionné sinon."""
    kind = str(objet.get("kind"))
    meta = dictionnaire(objet.get("metadata"))
    nom = f"{kind}/{meta.get('name', '?')}"
    bilan.objets.append(f"{fichier}#{document} {nom}")
    ajouter = constructeur(bilan, fichier, document, nom)
    if kind == "Secret" and (objet.get("data") or objet.get("stringData")):
        ajouter("secret-versionne", "info", "data", "Secret versionné : base64 n'est pas un "
                "chiffrement (préférer un coffre ou des secrets scellés)")
    if kind not in GABARITS:
        return
    chemin = GABARITS[kind]
    spec = descendre(objet, chemin)
    if isinstance(spec, dict):
        auditer_pod(spec, ".".join(chemin), kind, ajouter, bilan)


def constructeur(bilan: Bilan, fichier: str, document: int, nom: str) -> Any:
    """Fonction d'ajout de constats liée à un objet."""
    def ajouter(regle: str, gravite: str, chemin: str, message: str, conteneur: str = "") -> None:
        bilan.constats.append(Constat(fichier, document, nom, regle, gravite, chemin, message, conteneur))
    return ajouter


def auditer_pod(spec: dict[str, Any], chemin: str, kind: str, ajouter: Any, bilan: Bilan) -> None:
    """Règles de niveau pod, puis de chaque conteneur."""
    for champ in ("hostNetwork", "hostPID", "hostIPC"):
        if spec.get(champ) is True:
            ajouter(champ, "haute", f"{chemin}.{champ}", f"{champ}: true partage l'espace de noms de l'hôte")
    for i, volume in enumerate(spec.get("volumes") or []):
        if isinstance(volume, dict) and "hostPath" in volume:
            cible = dictionnaire(volume.get("hostPath")).get("path", "?")
            ajouter("hostPath", "haute", f"{chemin}.volumes[{i}].hostPath",
                    f"volume hostPath « {cible} » : accès au système de fichiers de l'hôte")
    contexte_pod = dictionnaire(spec.get("securityContext"))
    for groupe in ("containers", "initContainers", "ephemeralContainers"):
        for i, conteneur in enumerate(spec.get(groupe) or []):
            if isinstance(conteneur, dict):
                bilan.conteneurs += 1
                longue = kind in CHARGES_LONGUES and groupe == "containers"
                auditer_conteneur(conteneur, f"{chemin}.{groupe}[{i}]", contexte_pod, longue, ajouter)


def auditer_conteneur(c: dict[str, Any], chemin: str, contexte_pod: dict[str, Any],
                      longue: bool, ajouter: Any) -> None:
    """Toutes les règles d'un conteneur."""
    nom = str(c.get("name", "?"))
    def signaler(regle: str, gravite: str, champ: str, message: str) -> None:
        ajouter(regle, gravite, f"{chemin}.{champ}" if champ else chemin, message, nom)
    verifier_image(c.get("image"), signaler)
    verifier_ressources(dictionnaire(c.get("resources")), signaler)
    verifier_securite(dictionnaire(c.get("securityContext")), contexte_pod, signaler)
    if longue:
        for sonde in ("livenessProbe", "readinessProbe"):
            if sonde not in c:
                signaler("sonde-absente", "basse", sonde, f"{sonde} absente : panne ou démarrage non détectés")
    for i, variable in enumerate(c.get("env") or []):
        if isinstance(variable, dict):
            verifier_variable(variable, f"env[{i}]", signaler)


def verifier_image(image: Any, signaler: Any) -> None:
    """Image présente, avec étiquette autre que latest, ou épinglée par empreinte."""
    if not isinstance(image, str) or not image.strip():
        signaler("image-absente", "haute", "image", "image absente")
        return
    if "@" in image:
        return
    dernier = image.rsplit("/", 1)[-1]
    if ":" not in dernier:
        signaler("image-sans-etiquette", "moyenne", "image",
                 f"image « {image} » sans étiquette : équivaut à latest, non reproductible")
    elif dernier.rsplit(":", 1)[1] == "latest":
        signaler("image-latest", "moyenne", "image", f"image « {image} » : latest n'est pas reproductible")


def verifier_ressources(ressources: dict[str, Any], signaler: Any) -> None:
    """Limites et requests cpu/mémoire (une request absente vaut la limite si elle existe)."""
    limites = dictionnaire(ressources.get("limits"))
    demandes = dictionnaire(ressources.get("requests"))
    for ressource, gravite in (("memory", "moyenne"), ("cpu", "basse")):
        if ressource not in limites and ressource not in demandes:
            signaler(f"ressource-{ressource}-absente", gravite, "resources",
                     f"ni request ni limite {ressource} : ordonnancement et éviction imprévisibles")
        elif ressource not in limites:
            signaler(f"limite-{ressource}-absente", gravite, "resources.limits",
                     f"limite {ressource} absente : consommation non bornée")


def verifier_securite(sc: dict[str, Any], pod: dict[str, Any], signaler: Any) -> None:
    """privileged, escalade, utilisateur, système de fichiers racine, capacités."""
    if sc.get("privileged") is True:
        signaler("privileged", "haute", "securityContext.privileged", "conteneur privilégié")
    escalade = sc.get("allowPrivilegeEscalation")
    if escalade is True:
        signaler("escalade-privileges", "haute", "securityContext.allowPrivilegeEscalation",
                 "allowPrivilegeEscalation: true")
    elif escalade is not False:
        signaler("escalade-privileges", "moyenne", "securityContext.allowPrivilegeEscalation",
                 "allowPrivilegeEscalation non fixé à false (vrai par défaut)")
    utilisateur = sc.get("runAsUser", pod.get("runAsUser"))
    non_root = sc.get("runAsNonRoot", pod.get("runAsNonRoot"))
    if utilisateur == 0:
        signaler("root", "haute", "securityContext.runAsUser", "s'exécute en root (runAsUser: 0)")
    elif non_root is not True and not (isinstance(utilisateur, int) and utilisateur > 0):
        signaler("run-as-non-root", "moyenne", "securityContext.runAsNonRoot",
                 "runAsNonRoot absent (ni runAsUser non nul) : l'image peut tourner en root")
    if sc.get("readOnlyRootFilesystem") is not True:
        signaler("racine-en-ecriture", "basse", "securityContext.readOnlyRootFilesystem",
                 "système de fichiers racine inscriptible")
    ajoutees = dictionnaire(sc.get("capabilities")).get("add") or []
    dangereuses = sorted(str(c).upper().removeprefix("CAP_") for c in ajoutees
                         if str(c).upper().removeprefix("CAP_") in CAPACITES_DANGEREUSES)
    if dangereuses:
        signaler("capacites", "haute", "securityContext.capabilities.add",
                 f"capacités dangereuses ajoutées : {', '.join(dangereuses)}")


def verifier_variable(variable: dict[str, Any], champ: str, signaler: Any) -> None:
    """Secret en clair : nom évocateur ou forme de jeton connue ; la valeur n'est pas recopiée."""
    valeur = variable.get("value")
    if not isinstance(valeur, str) or not valeur.strip() or "valueFrom" in variable:
        return
    nom = str(variable.get("name", ""))
    forme = next((libelle for libelle, motif in FORMES_JETONS if motif.search(valeur)), None)
    if forme or nom_secret(nom):
        raison = f"forme de {forme}" if forme else "nom évocateur"
        signaler("secret-en-clair", "haute", f"{champ}.value",
                 f"variable {nom} : valeur en clair ({raison}, {len(valeur)} caractères masqués) ; "
                 "utiliser valueFrom.secretKeyRef")


def nom_secret(nom: str) -> bool:
    """Nom de variable qui désigne un secret (DB_PASSWORD, API_KEY…), hors suffixes innocents."""
    jetons = [j for j in re.split(r"[^A-Za-z0-9]+|(?<=[a-z])(?=[A-Z])", nom.upper()) if j]
    if not jetons or jetons[-1] in SUFFIXES_INNOCENTS:
        return False
    if any(j in JETONS_SECRETS for j in jetons):
        return True
    return any((a, b) == paire for a, b in zip(jetons, jetons[1:]) for paire in PAIRES_SECRETES)


# --------------------------------------------------------------------------- orchestration


def choisir_moteur(demande: str) -> str:
    """'pyyaml' si disponible (ou demandé), sinon 'stdlib' avec une ligne sur stderr."""
    if demande == "stdlib":
        return "stdlib"
    if yaml is None:
        if demande == "pyyaml":
            raise EntreeInvalide("--moteur pyyaml demandé mais PyYAML n'est pas installé")
        print("PyYAML absent : YAML lu par l'analyseur stdlib d'un sous-ensemble (refus "
              "explicite au-delà, code 2)", file=sys.stderr)
        return "stdlib"
    return "pyyaml"


def traiter(args: argparse.Namespace, moteur: str) -> Bilan:
    """Lit les sources (fichiers, dossiers, texte en ligne) et audite chaque objet."""
    bilan = Bilan()
    base = args.racine if args.racine is not None else Path.cwd()
    sources: list[tuple[str, Any]] = []
    if args.texte is not None:
        sources.append(("--texte", lambda: charger_texte(args.texte, "--texte", moteur)))
    for chemin, nom, explicite in collecter(args.chemins, base, bilan):
        sources.append((nom, lambda c=chemin, n=nom, e=explicite: lire_manifeste(c, n, e, moteur, bilan)))
    for nom, lire in sources:
        try:
            documents = lire()
        except (EntreeInvalide, OSError, RecursionError) as exc:
            bilan.invalides.append({"fichier": nom, "erreur": str(exc)})
            continue
        if documents is None:
            continue
        bilan.fichiers.append(nom)
        for numero, objet in objets_kubernetes(documents):
            auditer_objet(nom, numero, objet, bilan)
    return bilan


def construire_rapport(bilan: Bilan, moteur: str, seuil: str) -> dict[str, Any]:
    """Assemble le rapport JSON."""
    rang = GRAVITES.index(seuil)
    par_gravite = {g: sum(1 for c in bilan.constats if c.gravite == g) for g in GRAVITES}
    par_regle: dict[str, int] = {}
    for c in bilan.constats:
        par_regle[c.regle] = par_regle.get(c.regle, 0) + 1
    return {
        "outil": Path(__file__).stem,
        "python": platform.python_version(),
        "moteur": moteur,
        "denominateur": len(bilan.objets),
        "examines": bilan.objets[:MAX_EXAMINES],
        "examines_tronques": len(bilan.objets) > MAX_EXAMINES,
        "conteneurs_examines": bilan.conteneurs,
        "fichiers_lus": bilan.fichiers,
        "fichiers_ecartes": bilan.ecartes,
        "entrees_invalides": bilan.invalides,
        "seuil": seuil,
        "par_gravite": par_gravite,
        "par_regle": dict(sorted(par_regle.items(), key=lambda kv: -kv[1])),
        "constats_au_seuil": sum(1 for c in bilan.constats if GRAVITES.index(c.gravite) >= rang),
        "constats": [vars(c) for c in sorted(bilan.constats, key=lambda c: (-GRAVITES.index(c.gravite),
                                                                            c.fichier, c.document))],
    }


def afficher_humain(rapport: dict[str, Any]) -> None:
    """Constats par gravité décroissante, puis bilan."""
    for c in rapport["constats"]:
        conteneur = f" [{c['conteneur']}]" if c["conteneur"] else ""
        print(f"{c['gravite'].upper():<8} {c['fichier']}#{c['document']} {c['objet']}{conteneur} "
              f"{c['regle']} : {c['message']}")
    for e in rapport["fichiers_ecartes"]:
        print(f"écarté : {e['fichier']} — {e['raison']}")
    print(f"Bilan : {rapport['denominateur']} objet(s), {rapport['conteneurs_examines']} conteneur(s), "
          f"constats {rapport['par_gravite']}")


def afficher_json(objet: dict[str, Any]) -> None:
    """Un seul objet JSON sur stdout."""
    print(json.dumps(objet, ensure_ascii=False, indent=2, default=str))


def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande, aide en français."""
    parseur = argparse.ArgumentParser(
        prog=Path(__file__).name,
        description=f"Audite des manifestes Kubernetes ({FORMAT_YAML}, JSON ; fichiers, dossiers ou texte) : "
                    "ressources, image, contexte de sécurité, hostPath/hostNetwork, sondes, secrets "
                    "en clair. Code 1 si un constat atteint le seuil, 2 si une entrée est illisible.",
        epilog=f"Exemple : python {RACINE.name}/{Path(__file__).name} deploiement/ --seuil moyenne --json",
    )
    parseur.add_argument("chemins", nargs="*", help="fichiers .yaml/.yml/.json ou dossiers (récursif)")
    parseur.add_argument("--texte", default=None, help="manifeste en ligne (JSON ou YAML)")
    parseur.add_argument("--seuil", choices=GRAVITES, default="basse",
                         help="gravité minimale qui rend le code 1 (défaut : basse)")
    parseur.add_argument("--moteur", choices=("auto", "stdlib", "pyyaml"), default="auto",
                         help="auto : PyYAML s'il est installé, sinon le sous-ensemble stdlib")
    parseur.add_argument("--racine", type=Path, default=None,
                         help="dossier de base des chemins relatifs (défaut : dossier courant)")
    parseur.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    return parseur


def main(argv: Sequence[str] | None = None) -> int:
    """Point d'entrée : 0 conforme, 1 constat au seuil, 2 entrée invalide, 3 rien à examiner."""
    parseur = construire_parseur()
    args = parseur.parse_args(argv)
    if not args.chemins and args.texte is None:
        parseur.print_usage(sys.stderr)
        print("erreur : donner au moins un chemin ou --texte", file=sys.stderr)
        return 2
    try:
        moteur = choisir_moteur(args.moteur)
    except EntreeInvalide as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 2
    bilan = traiter(args, moteur)
    rapport = construire_rapport(bilan, moteur, args.seuil)
    rapport["contrat"] = extraire_contrat(__doc__ or "")
    for invalide in bilan.invalides:
        print(f"erreur : {invalide['fichier']} : {invalide['erreur']}", file=sys.stderr)
    code = 2 if bilan.invalides else (1 if rapport["constats_au_seuil"] else 0)
    if not bilan.objets and not bilan.invalides:
        print("dénominateur nul : aucun objet Kubernetes trouvé, rien à examiner", file=sys.stderr)
        code = 3
    rapport["code_sortie"] = code
    if args.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
