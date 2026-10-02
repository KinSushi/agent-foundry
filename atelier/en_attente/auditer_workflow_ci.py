"""Auditer un workflow GitHub Actions : ce qu'un attaquant extérieur pourrait en tirer.

Le workflow de ce dépôt (.github/workflows/verifier.yml, 18 lignes) tourne sur chaque
push et chaque pull request ; `auditer_workflow_ci.py .github/workflows/verifier.yml`
y relève 3 constats : 2 actions référencées par une étiquette mobile (@v4, @v5) que
leur propriétaire peut déplacer vers un autre code, et aucun bloc permissions, si bien
que le jeton GITHUB_TOKEN reçoit les droits par défaut du dépôt.

QUESTION
    Ce workflow GitHub Actions est-il exploitable par un attaquant (action détournée,
    jeton trop puissant, code d'une pull request exécuté avec des secrets, injection de
    script par un titre ou une branche) ?
MESURE
    Lecture du YAML (PyYAML si présent, sinon analyseur minimal intégré) puis règles :
    WF001 action ou workflow réutilisable non épinglé par un SHA complet de 40
    caractères (image docker:// sans empreinte) ; WF002 permissions absentes au niveau
    du workflow et d'au moins un job ; WF003 permissions write-all ; WF004
    pull_request_target ou workflow_run qui extrait la tête de la pull request
    (actions/checkout avec ref ou repository de la tête, gh pr checkout) ; WF005
    expression ${{ github.event.* }} contrôlable par l'attaquant (titre, corps,
    commentaire, nom de branche, message de commit, auteur...) interpolée dans run:
    ou dans le script d'actions/github-script ; WF006 secret écrit dans le journal
    (echo d'un secrets.X ou d'une variable d'env qui en vient, sauf redirection vers
    un fichier ou un programme ; encodage base64 ; toJSON(secrets)) ou interpolé
    dans un script ; WF007 continue-on-error: true. Chaque constat donne
    règle, ligne, gravité, job, étape et correction.
    Analyseur minimal (repli stdlib) : indentation par espaces, mappages et listes en
    bloc (y compris « - clé: valeur » et liste au niveau de sa clé), scalaires nus,
    entre apostrophes ou guillemets (sur une ligne), scalaires nus sur plusieurs lignes,
    blocs | et > avec indicateurs (-, +, chiffre), collections en ligne [a, b] et
    {a: b} éventuellement sur plusieurs lignes, commentaires, marqueur --- initial. Il
    refuse explicitement (code 2) ancres, alias, étiquettes, clés complexes, clés de
    fusion, directives, documents multiples, tabulations d'indentation et chaînes
    citées sur plusieurs lignes.
HYPOTHÈSES
    Le fichier est un workflow (clés on et jobs) ou une action composite (runs.using:
    composite) ; les expressions ${{ }} ne sont pas construites dynamiquement ; les
    propriétaires actions/ et github/ sont ceux de GitHub.
LIMITES
    Ne suit pas les données contrôlées par l'attaquant à travers les sorties d'étapes
    (steps.x.outputs), les fichiers ou les variables d'environnement ; ne lit pas le
    code des actions appelées ni les workflows réutilisables distants ; ne connaît ni
    les permissions par défaut réglées dans le dépôt, ni les règles de protection des
    branches, ni les environnements à approbation ; les entrées workflow_dispatch et
    workflow_call (inputs.*) ne sont pas traitées comme hostiles.
CONTRE-EXEMPLES
    Faux négatif constaté : sous pull_request_target, un titre de pull request recopié
    dans une variable d'environnement de l'étape (env: T: ${{
    github.event.pull_request.title }}) puis évalué par « run: eval $T » n'est pas
    signalé (code 0). Faux positif constaté : « run: echo ${{ github.head_ref }} »
    dans un workflow déclenché seulement par push est signalé WF005 (élevée) alors
    que github.head_ref y est toujours vide.
INVOCATION
    {outil} --contenu "{on: push, jobs: {t: {runs-on: ubuntu-latest, steps: [{uses: actions/checkout@v4}]}}}" --json
DOMAINE
    Fichiers .github/workflows/*.yml et actions composites (action.yml) de GitHub
    Actions, relus avant fusion ou lors d'un audit de la chaîne d'approvisionnement.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    import yaml
except ImportError:
    yaml = None

RACINE = Path(__file__).resolve().parent

__all__ = ["analyser_workflow", "lire_yaml_minimal", "main"]

TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_INVOCATION = "INVOCATION"
TITRES_CONTRAT = ("QUESTION", "MESURE", TITRE_HYPOTHESES, "LIMITES",
                  "CONTRE-EXEMPLES", TITRE_INVOCATION, "DOMAINE")
REGLE_EPINGLAGE = "WF001"
REGLE_PERMISSIONS = "WF002"
REGLE_WRITE_ALL = "WF003"
REGLE_TETE_PR = "WF004"
REGLE_INJECTION = "WF005"
REGLE_SECRET = "WF006"
REGLE_CONTINUE = "WF007"
JETON_GITHUB = "GITHUB_TOKEN"
FORMAT_YAML = "YAML"
GRAVITES = ("info", "faible", "moyenne", "élevée", "critique")

CODE_RIEN = 0
CODE_TROUVE = 1
CODE_USAGE = 2
CODE_VIDE = 3
TAILLE_MAX = 2_000_000
PROPRIETAIRES_GITHUB = frozenset({"actions", "github"})
DECLENCHEURS_PRIVILEGIES = frozenset({"pull_request_target", "workflow_run"})

MOTIF_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.S)
MOTIF_HOSTILE = re.compile(
    r"github\.event\.(?:issue|pull_request|discussion)\.(?:title|body)\b"
    r"|github\.event\.(?:comment|review|review_comment)\.body\b"
    r"|github\.event\.pages(?:\[[^\]]*\]|\.\*)\.page_name\b"
    r"|github\.event\.commits(?:\[[^\]]*\]|\.\*)\.(?:message|author\.(?:email|name))\b"
    r"|github\.event\.head_commit\.(?:message|author\.(?:email|name))\b"
    r"|github\.event\.pull_request\.head\.(?:ref|label|repo\.default_branch)\b"
    r"|github\.head_ref\b"
    r"|github\.event\.workflow_run\.(?:head_branch|display_title|head_commit\.(?:message|author\.(?:email|name)))\b")
MOTIF_TETE_PR = re.compile(
    r"github\.event\.pull_request\.head\.(?:sha|ref|repo\.full_name)|github\.head_ref|refs/pull/"
    r"|github\.event\.pull_request\.merge_commit_sha|github\.event\.workflow_run\.head_(?:sha|branch)"
    r"|github\.event\.number")
MOTIF_CHECKOUT_SHELL = re.compile(r"\bgh\s+pr\s+checkout\b|\bgit\s+(?:fetch|checkout)\b[^\n]*(?:pull/|head_ref|head\.sha)")
MOTIF_SECRET = re.compile(r"\bsecrets\.([A-Za-z_][A-Za-z0-9_]*)")
MOTIF_TOUS_SECRETS = re.compile(r"(?i)\btojson\(\s*secrets\s*\)")
MOTIF_AFFICHAGE = re.compile(r"\b(?:echo|printf|print|cat|tee|puts|Write-Host|Write-Output|console\.log)\b")
MOTIF_VERS_FICHIER = re.compile(r"(?<![0-9&<])>{1,2}\s*(?!&)\S")
MOTIF_VERS_PROGRAMME = re.compile(r"\|\s*(?!tee\b)[A-Za-z]")
MOTIF_SHA = re.compile(r"[0-9a-f]{40}")
MOTIF_CLE = re.compile(r"([^\s#'\"\[\]{},&*!|>%@`?-][^#]*?|-[^\s#][^#]*?)\s*:(?:\s+|$)")


class ErreurYaml(ValueError):
    """Construction YAML que l'analyseur minimal refuse de lire."""

    def __init__(self, ligne: int, message: str) -> None:
        super().__init__(f"ligne {ligne} : {message}")
        self.ligne = ligne


@dataclass(frozen=True)
class Scalaire:
    """Valeur scalaire ; premiere_ligne est la ligne de son premier caractère de contenu."""

    texte: str
    ligne: int
    premiere_ligne: int = 0


@dataclass
class Mappage:
    """Mappage YAML : clé -> nœud, et ligne de chaque clé."""

    ligne: int
    entrees: dict[str, object] = field(default_factory=dict)
    lignes_cles: dict[str, int] = field(default_factory=dict)


@dataclass
class Sequence:
    """Liste YAML."""

    ligne: int
    elements: list[object] = field(default_factory=list)


@dataclass(frozen=True)
class Constat:
    """Un défaut exploitable : règle, ligne, gravité, emplacement, correction."""

    fichier: str
    ligne: int
    regle: str
    gravite: str
    message: str
    correction: str
    job: str = ""
    etape: str = ""


@dataclass
class Curseur:
    """Position de lecture dans les lignes du document."""

    lignes: list[str]
    i: int = 0


# ---------------------------------------------------------------- analyseur minimal

def _indentation(ligne: str, numero: int) -> int:
    """Nombre d'espaces de tête ; une tabulation dans l'indentation est refusée."""
    tete = ligne[: len(ligne) - len(ligne.lstrip(" \t"))]
    if "\t" in tete:
        raise ErreurYaml(numero, "tabulation dans l'indentation (interdite en YAML)")
    return len(tete)


def _significative(ligne: str) -> bool:
    """Ni vide, ni commentaire seul."""
    contenu = ligne.strip()
    return bool(contenu) and not contenu.startswith("#")


def _prochaine(c: Curseur) -> int | None:
    """Indice de la prochaine ligne significative, sans la consommer."""
    j = c.i
    while j < len(c.lignes) and not _significative(c.lignes[j]):
        j += 1
    return j if j < len(c.lignes) else None


def _sans_commentaire(texte: str) -> str:
    """Retire un commentaire « # » hors guillemets (précédé d'un blanc ou en tête)."""
    guillemet = ""
    for k, ch in enumerate(texte):
        if guillemet:
            if ch == guillemet:
                guillemet = ""
        elif ch in "'\"" and (k == 0 or texte[k - 1] in " ,[{:"):
            guillemet = ch
        elif ch == "#" and (k == 0 or texte[k - 1] in " \t"):
            return texte[:k].rstrip()
    return texte.rstrip()


def _refuser_indicateurs(texte: str, numero: int) -> None:
    """Ancres, alias, étiquettes, clés complexes et directives ne sont pas lus."""
    noms = {"&": "ancre", "*": "alias", "!": "étiquette", "?": "clé complexe", "%": "directive",
            "@": "caractère réservé @", "`": "caractère réservé `"}
    if texte[:1] in noms and not (texte[:1] == "?" and len(texte) > 1 and texte[1] != " "):
        raise ErreurYaml(numero, f"{noms[texte[0]]} non prise en charge par l'analyseur minimal "
                                 "(installer PyYAML)")


def _cite(texte: str, numero: int) -> tuple[str, str]:
    """Lit une chaîne citée en tête de texte ; rend (valeur, reste)."""
    q = texte[0]
    k = 1
    morceaux: list[str] = []
    while k < len(texte):
        ch = texte[k]
        if q == "'" and ch == "'" and texte[k + 1: k + 2] == "'":
            morceaux.append("'")
            k += 2
        elif ch == q:
            return "".join(morceaux), texte[k + 1:]
        elif q == '"' and ch == "\\" and k + 1 < len(texte):
            car, k = _echappement(texte, k + 1, numero)
            morceaux.append(car)
        else:
            morceaux.append(ch)
            k += 1
    raise ErreurYaml(numero, "chaîne citée sur plusieurs lignes non prise en charge (installer PyYAML)")


def _echappement(texte: str, k: int, numero: int) -> tuple[str, int]:
    """Séquence d'échappement d'une chaîne entre guillemets ; rend (caractère, position suivante)."""
    simples = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\"": "\"", "\\": "\\", "/": "/",
               " ": " ", "e": "\x1b", "a": "\a", "b": "\b", "N": "\x85", "_": "\xa0"}
    lettre = texte[k]
    if lettre in simples:
        return simples[lettre], k + 1
    largeur = {"x": 2, "u": 4, "U": 8}.get(lettre, 0)
    code = texte[k + 1: k + 1 + largeur]
    if not largeur or len(code) != largeur or any(c not in "0123456789abcdefABCDEF" for c in code):
        raise ErreurYaml(numero, f"échappement « \\{lettre} » invalide dans une chaîne entre guillemets")
    return chr(int(code, 16)), k + 1 + largeur


def _scalaire_ligne(texte: str, numero: int) -> Scalaire:
    """Scalaire tenant sur une ligne : nu ou cité."""
    _refuser_indicateurs(texte, numero)
    if texte == "-" or texte.startswith("- "):
        raise ErreurYaml(numero, "entrée de liste inattendue après une clé")
    if texte[:1] in "'\"":
        valeur, reste = _cite(texte, numero)
        if _sans_commentaire(reste).strip():
            raise ErreurYaml(numero, f"texte inattendu après une chaîne citée : {reste.strip()[:40]}")
        return Scalaire(valeur, numero, numero)
    return Scalaire(_nu_valide(_sans_commentaire(texte), numero), numero, numero)


def _nu_valide(texte: str, numero: int) -> str:
    """Un scalaire nu ne peut contenir « : » suivi d'une espace ni finir par « : »."""
    if ": " in texte or texte.endswith(":"):
        raise ErreurYaml(numero, "« : » dans une valeur non citée (YAML invalide : citer la valeur)")
    return texte


def _equilibre(texte: str) -> int:
    """Profondeur de crochets et d'accolades ouverts hors guillemets."""
    profondeur = 0
    guillemet = ""
    for k, ch in enumerate(texte):
        if guillemet:
            guillemet = "" if ch == guillemet else guillemet
        elif ch in "'\"" and (k == 0 or texte[k - 1] in " ,[{:"):
            guillemet = ch
        elif ch in "[{":
            profondeur += 1
        elif ch in "]}":
            profondeur -= 1
    return profondeur


def _flux_valeur(texte: str, k: int, numero: int) -> tuple[object, int]:
    """Valeur en ligne : [liste], {mappage}, chaîne citée ou nue ; rend (nœud, position)."""
    while k < len(texte) and texte[k] == " ":
        k += 1
    if k >= len(texte):
        raise ErreurYaml(numero, "collection en ligne inachevée")
    if texte[k] in "[{":
        return _flux_collection(texte, k, numero)
    if texte[k] in "'\"":
        valeur, reste = _cite(texte[k:], numero)
        return Scalaire(valeur, numero, numero), len(texte) - len(reste)
    _refuser_indicateurs(texte[k:], numero)
    fin = k
    while fin < len(texte) and texte[fin] not in ",[]{}" and not _fin_cle_flux(texte, fin):
        fin += 1
    return Scalaire(texte[k:fin].strip(), numero, numero), fin


def _fin_cle_flux(texte: str, k: int) -> bool:
    """Un deux-points suivi d'une espace ou d'un indicateur termine une clé en ligne."""
    return texte[k] == ":" and texte[k + 1: k + 2] in (" ", ",", "]", "}", "")


def _flux_collection(texte: str, k: int, numero: int) -> tuple[object, int]:
    """Collection en ligne ouverte en texte[k]."""
    fermant = "]" if texte[k] == "[" else "}"
    noeud: object = Sequence(numero) if fermant == "]" else Mappage(numero)
    k += 1
    while True:
        while k < len(texte) and texte[k] == " ":
            k += 1
        if k < len(texte) and texte[k] == fermant:
            return noeud, k + 1
        k = _flux_element(noeud, texte, k, numero)
        while k < len(texte) and texte[k] == " ":
            k += 1
        if k < len(texte) and texte[k] == ",":
            k += 1
        elif k >= len(texte) or texte[k] != fermant:
            raise ErreurYaml(numero, "collection en ligne mal formée")


def _flux_element(noeud: object, texte: str, k: int, numero: int) -> int:
    """Ajoute un élément (liste) ou une paire (mappage) ; rend la position suivante."""
    valeur, k = _flux_valeur(texte, k, numero)
    if isinstance(noeud, Sequence):
        noeud.elements.append(valeur)
        return k
    if not isinstance(valeur, Scalaire):
        raise ErreurYaml(numero, "clé non scalaire dans un mappage en ligne")
    while k < len(texte) and texte[k] == " ":
        k += 1
    contenu: object = Scalaire("", numero, numero)
    if k < len(texte) and texte[k] == ":":
        k += 1
        if texte[k: k + 1] not in (",", "}"):
            contenu, k = _flux_valeur(texte, k, numero)
    noeud.entrees[valeur.texte] = contenu
    noeud.lignes_cles[valeur.texte] = numero
    return k


def _flux(c: Curseur, debut: str, numero: int) -> object:
    """Collection en ligne, éventuellement poursuivie sur les lignes suivantes."""
    texte = _sans_commentaire(debut)
    while _equilibre(texte) > 0 and c.i < len(c.lignes):
        texte += " " + _sans_commentaire(c.lignes[c.i].strip())
        c.i += 1
    noeud, k = _flux_collection(texte, 0, numero)
    if texte[k:].strip():
        raise ErreurYaml(numero, f"texte inattendu après une collection : {texte[k:].strip()[:40]}")
    return noeud


def _bloc_scalaire(c: Curseur, entete: str, parent: int, numero: int) -> Scalaire:
    """Bloc | ou > avec indicateurs de troncature (-, +) et d'indentation (chiffre)."""
    m = re.fullmatch(r"([|>])([+-]?)([1-9]?)([+-]?)", _sans_commentaire(entete))
    if not m:
        raise ErreurYaml(numero, f"en-tête de bloc illisible : {entete[:20]}")
    style, chomp = m.group(1), m.group(2) or m.group(4)
    retrait = parent + int(m.group(3)) if m.group(3) else 0
    contenu: list[str] = []
    while c.i < len(c.lignes):
        ligne = c.lignes[c.i]
        if ligne.strip():
            ind = len(ligne) - len(ligne.lstrip(" "))
            retrait = retrait or ind
            if ind < retrait or ind <= parent:
                break
        contenu.append(ligne[retrait:] if ligne.strip() else "")
        c.i += 1
    texte = _plier(contenu) if style == ">" else "\n".join(contenu)
    return Scalaire(_tronquer(texte, chomp), numero, numero + 1)


def _plier(lignes: list[str]) -> str:
    """Pliage du style > : saut entre deux lignes -> espace ; k lignes vides -> k sauts."""
    sortie = ""
    vides = 0
    precedente = ""
    for ligne in lignes:
        if not ligne:
            vides += 1
            continue
        if not precedente or vides:
            sortie += "\n" * vides + ligne
        elif ligne.startswith(" ") or precedente.startswith(" "):
            sortie += "\n" + ligne
        else:
            sortie += " " + ligne
        vides, precedente = 0, ligne
    return sortie + "\n" * vides


def _tronquer(texte: str, chomp: str) -> str:
    """Indicateur de troncature : - aucun saut final, + tous, défaut un seul."""
    nu = texte.rstrip("\n")
    if chomp == "-":
        return nu
    return texte + "\n" if chomp == "+" else nu + "\n"


def _valeur(c: Curseur, reste: str, parent: int, numero: int) -> object:
    """Valeur d'une clé ou d'un élément de liste, selon ce qui suit le séparateur."""
    reste = reste.strip()
    if not reste or reste.startswith("#"):
        return _valeur_imbriquee(c, parent, numero)
    if reste[0] in "|>":
        return _bloc_scalaire(c, reste, parent, numero)
    if reste[0] in "[{":
        return _flux(c, reste, numero)
    scalaire = _scalaire_ligne(reste, numero)
    if reste[0] in "'\"":
        return scalaire
    return _poursuivre_nu(c, scalaire, parent)


def _poursuivre_nu(c: Curseur, scalaire: Scalaire, parent: int) -> Scalaire:
    """Scalaire nu poursuivi sur des lignes plus indentées (joint par des espaces)."""
    morceaux = [scalaire.texte]
    while (j := _prochaine(c)) is not None and _indentation(c.lignes[j], j + 1) > parent:
        morceaux.append(_nu_valide(_sans_commentaire(c.lignes[j].strip()), j + 1))
        c.i = j + 1
    return Scalaire(" ".join(morceaux), scalaire.ligne, scalaire.premiere_ligne)


def _valeur_imbriquee(c: Curseur, parent: int, numero: int) -> object:
    """Valeur sur les lignes suivantes : bloc plus indenté, ou liste au niveau de la clé."""
    j = _prochaine(c)
    if j is None:
        return Scalaire("", numero, numero)
    ind = _indentation(c.lignes[j], j + 1)
    tete = c.lignes[j].strip()
    if ind > parent or (ind == parent and (tete == "-" or tete.startswith("- "))):
        return _bloc(c, ind)
    return Scalaire("", numero, numero)


def _bloc(c: Curseur, ind: int) -> object:
    """Nœud en bloc dont la première ligne significative est à l'indentation ind."""
    j = _prochaine(c)
    tete = c.lignes[j][ind:]
    if tete == "-" or tete.startswith("- "):
        return _sequence(c, ind)
    if MOTIF_CLE.match(tete) or tete[:1] in "'\"" and re.match(r"(['\"]).*?\1\s*:(?:\s|$)", tete):
        return _mappage(c, ind)
    c.i = j + 1
    return _valeur(c, tete, ind - 1, j + 1)


def _cle(tete: str, numero: int) -> tuple[str, str]:
    """Sépare « clé: reste » ; la clé peut être citée."""
    _refuser_indicateurs(tete, numero)
    if tete[:1] in "'\"":
        cle, reste = _cite(tete, numero)
        reste = reste.lstrip()
        if not reste.startswith(":"):
            raise ErreurYaml(numero, "deux-points attendu après une clé citée")
        return cle, reste[1:]
    m = MOTIF_CLE.match(tete)
    if not m:
        raise ErreurYaml(numero, f"ligne illisible dans un mappage : {tete[:40]}")
    if m.group(1).strip() == "<<":
        raise ErreurYaml(numero, "clé de fusion << non prise en charge (installer PyYAML)")
    return m.group(1).strip(), tete[m.end():]


def _mappage(c: Curseur, ind: int) -> Mappage:
    """Mappage en bloc à l'indentation ind."""
    j = _prochaine(c)
    noeud = Mappage(j + 1)
    while (j := _prochaine(c)) is not None:
        courant = _indentation(c.lignes[j], j + 1)
        if courant < ind:
            break
        tete = c.lignes[j][ind:]
        if courant > ind or tete.startswith("- ") or tete == "-":
            raise ErreurYaml(j + 1, "indentation inattendue dans un mappage")
        cle, reste = _cle(tete, j + 1)
        c.i = j + 1
        noeud.entrees[cle] = _valeur(c, reste, ind, j + 1)
        noeud.lignes_cles[cle] = j + 1
    return noeud


def _sequence(c: Curseur, ind: int) -> Sequence:
    """Liste en bloc à l'indentation ind ; « - clé: v » ouvre un mappage dans l'élément."""
    j = _prochaine(c)
    noeud = Sequence(j + 1)
    while (j := _prochaine(c)) is not None:
        courant = _indentation(c.lignes[j], j + 1)
        tete = c.lignes[j][ind:]
        if courant != ind or not (tete == "-" or tete.startswith("- ")):
            if courant > ind:
                raise ErreurYaml(j + 1, "indentation inattendue dans une liste")
            break
        reste = tete[1:]
        decalage = len(reste) - len(reste.lstrip(" "))
        interieur = reste.strip()
        if interieur and not interieur.startswith("#") and (interieur.startswith("- ") or MOTIF_CLE.match(interieur)) and interieur[0] not in "[{'\"":
            c.lignes[j] = c.lignes[j][:ind] + " " + c.lignes[j][ind + 1:]
            noeud.elements.append(_bloc(c, ind + 1 + decalage))
        else:
            c.i = j + 1
            noeud.elements.append(_valeur(c, reste, ind, j + 1))
    return noeud


def _documents(lignes: list[str]) -> list[str]:
    """Retire le marqueur --- initial et la fin « ... » ; refuse plusieurs documents."""
    debut = 0
    for k, ligne in enumerate(lignes):
        if not _significative(ligne):
            continue
        if ligne.rstrip() == "---":
            debut = k + 1
        break
    corps = lignes[debut:]
    for k, ligne in enumerate(corps):
        if ligne.rstrip() == "...":
            return [""] * debut + corps[:k]
        if ligne.startswith("---") and ligne[3:4] in ("", " "):
            raise ErreurYaml(debut + k + 1, f"plusieurs documents {FORMAT_YAML} (installer PyYAML)")
    return [""] * debut + corps


def lire_yaml_minimal(texte: str) -> object:
    """Analyseur minimal (sous-ensemble documenté de YAML) ; lève ErreurYaml sinon."""
    lignes = _documents(texte.splitlines())
    c = Curseur(lignes)
    j = _prochaine(c)
    if j is None:
        return Scalaire("", 1, 1)
    racine = _bloc(c, _indentation(lignes[j], j + 1))
    if (reste := _prochaine(c)) is not None:
        raise ErreurYaml(reste + 1, "contenu après la fin du nœud racine (indentation incohérente)")
    return racine


# ---------------------------------------------------------------- PyYAML

def _depuis_pyyaml(noeud: object, profondeur: int = 0) -> object:
    """Convertit un nœud composé par PyYAML (avec ses lignes) vers le modèle commun."""
    if profondeur > 200:
        raise ValueError("structure YAML trop profonde ou récursive")
    ligne = noeud.start_mark.line + 1
    if isinstance(noeud, yaml.ScalarNode):
        premiere = ligne + 1 if noeud.style in ("|", ">") else ligne
        return Scalaire(str(noeud.value), ligne, premiere)
    if isinstance(noeud, yaml.SequenceNode):
        return Sequence(ligne, [_depuis_pyyaml(e, profondeur + 1) for e in noeud.value])
    resultat = Mappage(ligne)
    for cle, valeur in noeud.value:
        converti = _depuis_pyyaml(valeur, profondeur + 1)
        if cle.tag == "tag:yaml.org,2002:merge" and isinstance(converti, Mappage):
            for k, v in converti.entrees.items():
                resultat.entrees.setdefault(k, v)
                resultat.lignes_cles.setdefault(k, converti.lignes_cles[k])
            continue
        resultat.entrees[str(cle.value)] = converti
        resultat.lignes_cles[str(cle.value)] = cle.start_mark.line + 1
    return resultat


def lire_yaml(texte: str, avec_pyyaml: bool) -> object:
    """Lit le YAML avec PyYAML si demandé, sinon avec l'analyseur minimal."""
    if not avec_pyyaml:
        return lire_yaml_minimal(texte)
    try:
        noeud = yaml.compose(texte, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise ValueError(f"{FORMAT_YAML} invalide : {str(exc).splitlines()[0]}") from exc
    return Scalaire("", 1, 1) if noeud is None else _depuis_pyyaml(noeud)


# ---------------------------------------------------------------- règles

def _texte(noeud: object) -> str:
    """Texte d'un scalaire, chaîne vide sinon."""
    return noeud.texte if isinstance(noeud, Scalaire) else ""


def _declencheurs(racine: Mappage) -> set[str]:
    """Événements déclencheurs (on: chaîne, liste ou mappage)."""
    on = racine.entrees.get("on", racine.entrees.get("true"))
    if isinstance(on, Scalaire):
        return {on.texte}
    if isinstance(on, Sequence):
        return {_texte(e) for e in on.elements}
    return set(on.entrees) if isinstance(on, Mappage) else set()


def _nom_etape(etape: Mappage, rang: int) -> str:
    """Nom lisible d'une étape."""
    for cle in ("name", "id", "uses", "run"):
        if texte := _texte(etape.entrees.get(cle)):
            return texte.splitlines()[0][:60]
    return f"étape {rang}"


def _regle_epinglage(uses: Scalaire, fichier: str, job: str, etape: str) -> Iterator[Constat]:
    """WF001 : action, workflow réutilisable ou image docker non épinglés."""
    ref = uses.texte.strip()
    if ref.startswith(("./", ".\\")) or not ref:
        return
    if ref.startswith("docker://"):
        if "@sha256:" not in ref:
            yield Constat(fichier, uses.ligne, REGLE_EPINGLAGE, "moyenne",
                          f"image {ref} sans empreinte : son contenu peut changer", f"{ref}@sha256:<empreinte>",
                          job, etape)
        return
    action, _, version = ref.partition("@")
    if MOTIF_SHA.fullmatch(version):
        return
    proprietaire = action.split("/", 1)[0].lower()
    gravite = "faible" if proprietaire in PROPRIETAIRES_GITHUB else "moyenne"
    if not version or not any(ch.isdigit() for ch in version):
        gravite = "élevée"
    nature = "SHA abrégé" if re.fullmatch(r"[0-9a-f]{7,39}", version) else f"référence mobile « {version or 'aucune'} »"
    yield Constat(fichier, uses.ligne, REGLE_EPINGLAGE, gravite,
                  f"{action} épinglée par {nature} : son propriétaire peut y substituer un autre code",
                  f"uses: {action}@<SHA de 40 caractères>  # {version or 'version'}  "
                  f"(git ls-remote https://github.com/{'/'.join(action.split('/')[:2])})", job, etape)


def _lignes_expressions(scalaire: Scalaire) -> Iterator[tuple[int, str, str]]:
    """(ligne, expression, ligne de script) de chaque ${{ }} d'un scalaire."""
    for k, ligne in enumerate(scalaire.texte.split("\n")):
        for m in MOTIF_EXPRESSION.finditer(ligne):
            yield (scalaire.premiere_ligne or scalaire.ligne) + k, m.group(1).strip(), ligne


def _regle_injection(script: Scalaire, contexte: tuple[str, str, str, set[str]]) -> Iterator[Constat]:
    """WF005 : expression contrôlable par l'attaquant interpolée dans un script."""
    fichier, job, etape, declencheurs = contexte
    for ligne, expression, _ in _lignes_expressions(script):
        m = MOTIF_HOSTILE.search(expression)
        if not m:
            continue
        gravite = "critique" if declencheurs & DECLENCHEURS_PRIVILEGIES else "élevée"
        yield Constat(fichier, ligne, REGLE_INJECTION, gravite,
                      f"${{{{ {expression[:80]} }}}} est contrôlé par l'auteur de l'événement et "
                      "interpolé tel quel dans le script : injection de commandes",
                      f"env: VALEUR: ${{{{ {m.group()} }}}}  puis \"$VALEUR\" (entre guillemets) "
                      "dans le script", job, etape)


def _sortie_journal(ligne: str) -> str:
    """« encodé », « affiché » ou '' selon que la ligne écrit dans le journal du job."""
    if "::add-mask::" in ligne or MOTIF_VERS_FICHIER.search(ligne):
        return ""
    if "base64" in ligne:
        return "encodé"
    if MOTIF_AFFICHAGE.search(ligne) and not MOTIF_VERS_PROGRAMME.search(ligne):
        return "affiché"
    return ""


def _regle_secrets_script(script: Scalaire, variables: set[str],
                          contexte: tuple[str, str, str, set[str]]) -> Iterator[Constat]:
    """WF006 dans un script : secret affiché, encodé ou interpolé."""
    fichier, job, etape, _ = contexte
    base = script.premiere_ligne or script.ligne
    for k, ligne in enumerate(script.texte.split("\n")):
        interpole = MOTIF_SECRET.search(" ".join(MOTIF_EXPRESSION.findall(ligne)))
        via_env = [v for v in sorted(variables) if re.search(rf"\$\{{?{v}\b|\$env:{v}\b|%{v}%", ligne)]
        sortie = _sortie_journal(ligne) if (interpole or via_env) else ""
        if sortie:
            source = f"secrets.{interpole.group(1)}" if interpole else f"${via_env[0]} (issue d'un secret)"
            yield Constat(fichier, base + k, REGLE_SECRET, "élevée" if sortie == "encodé" else "moyenne",
                          f"{source} {sortie} dans le journal du job"
                          + (" : la forme encodée échappe au masquage de GitHub" if sortie == "encodé"
                             else " : seul le masquage de GitHub le protège"),
                          "ne jamais écrire un secret dans le journal ; le passer par env: au seul "
                          "programme qui en a besoin", job, etape)
        elif interpole:
            yield Constat(fichier, base + k, REGLE_SECRET, "faible",
                          f"secrets.{interpole.group(1)} interpolé dans le texte du script "
                          "(visible dans le fichier de script et la liste des processus)",
                          f"env: {interpole.group(1)}: ${{{{ secrets.{interpole.group(1)} }}}} puis "
                          f"\"${interpole.group(1)}\"", job, etape)


def _variables_secretes(*mappages: object) -> set[str]:
    """Noms de variables d'env dont la valeur provient de secrets.*."""
    noms: set[str] = set()
    for env in mappages:
        if isinstance(env, Mappage):
            noms |= {k for k, v in env.entrees.items() if "secrets." in _texte(v)}
    return noms


def _tous_secrets(noeud: object, fichier: str) -> Iterator[Constat]:
    """WF006 : toJSON(secrets) n'importe où dans le fichier."""
    if isinstance(noeud, Scalaire) and MOTIF_TOUS_SECRETS.search(noeud.texte):
        yield Constat(fichier, noeud.ligne, REGLE_SECRET, "élevée",
                      "toJSON(secrets) sérialise tous les secrets du dépôt dans une valeur",
                      "ne référencer que les secrets nécessaires, un par un")
    elif isinstance(noeud, Mappage):
        for valeur in noeud.entrees.values():
            yield from _tous_secrets(valeur, fichier)
    elif isinstance(noeud, Sequence):
        for valeur in noeud.elements:
            yield from _tous_secrets(valeur, fichier)


def _regle_continue(noeud: Mappage, fichier: str, job: str, etape: str) -> Iterator[Constat]:
    """WF007 : continue-on-error: true masque un échec."""
    valeur = noeud.entrees.get("continue-on-error")
    if isinstance(valeur, Scalaire) and valeur.texte.strip().lower() == "true":
        cible = f"l'étape « {etape} »" if etape else f"le job « {job} »"
        yield Constat(fichier, valeur.ligne, REGLE_CONTINUE, "faible",
                      f"continue-on-error: true : un échec de {cible} (analyse de sécurité, test) passe inaperçu",
                      "retirer continue-on-error, ou le limiter à une étape informative explicitement nommée",
                      job, etape)


def _regle_tete_pr(etape: Mappage, contexte: tuple[str, str, str, set[str]]) -> Iterator[Constat]:
    """WF004 : extraction du code de la pull request dans un contexte privilégié."""
    fichier, job, nom, declencheurs = contexte
    privilegies = declencheurs & DECLENCHEURS_PRIVILEGIES
    if not privilegies:
        return
    uses = _texte(etape.entrees.get("uses"))
    avec = etape.entrees.get("with")
    refs = " ".join(_texte(avec.entrees.get(k)) for k in ("ref", "repository")) if isinstance(avec, Mappage) else ""
    run = etape.entrees.get("run")
    tete = (uses.startswith("actions/checkout") and MOTIF_TETE_PR.search(refs)) or (
        isinstance(run, Scalaire) and MOTIF_CHECKOUT_SHELL.search(run.texte))
    if tete:
        gravite = "critique" if "pull_request_target" in privilegies else "élevée"
        ligne = etape.lignes_cles.get("with", etape.lignes_cles.get("run", etape.ligne))
        yield Constat(fichier, ligne, REGLE_TETE_PR, gravite,
                      f"{'/'.join(sorted(privilegies))} extrait le code de la pull request : ce code "
                      "s'exécute avec les secrets et un jeton en écriture",
                      "utiliser pull_request (sans secrets) pour construire le code proposé ; réserver "
                      "pull_request_target aux étapes qui ne l'exécutent pas", job, nom)


def _regles_etape(etape: Mappage, rang: int, env_job: set[str],
                  contexte: tuple[str, str, set[str]]) -> Iterator[Constat]:
    """Toutes les règles d'une étape."""
    fichier, job, declencheurs = contexte
    nom = _nom_etape(etape, rang)
    ctx = (fichier, job, nom, declencheurs)
    if isinstance(uses := etape.entrees.get("uses"), Scalaire):
        yield from _regle_epinglage(uses, fichier, job, nom)
    variables = env_job | _variables_secretes(etape.entrees.get("env"))
    if isinstance(run := etape.entrees.get("run"), Scalaire):
        yield from _regle_injection(run, ctx)
        yield from _regle_secrets_script(run, variables, ctx)
    avec = etape.entrees.get("with")
    if "github-script" in _texte(uses) and isinstance(avec, Mappage):
        if isinstance(script := avec.entrees.get("script"), Scalaire):
            yield from _regle_injection(script, ctx)
    yield from _regle_tete_pr(etape, ctx)
    yield from _regle_continue(etape, fichier, job, nom)


def _regles_job(job: str, corps: Mappage, env_global: set[str],
                contexte: tuple[str, set[str]]) -> Iterator[Constat]:
    """Règles d'un job : appel de workflow réutilisable, permissions, étapes."""
    fichier, declencheurs = contexte
    if isinstance(uses := corps.entrees.get("uses"), Scalaire):
        yield from _regle_epinglage(uses, fichier, job, "")
    yield from _regle_write_all(corps.entrees.get("permissions"), fichier, job)
    yield from _regle_continue(corps, fichier, job, "")
    env_job = env_global | _variables_secretes(corps.entrees.get("env"))
    etapes = corps.entrees.get("steps")
    if isinstance(etapes, Sequence):
        for rang, etape in enumerate(etapes.elements, 1):
            if isinstance(etape, Mappage):
                yield from _regles_etape(etape, rang, env_job, (fichier, job, declencheurs))


def _regle_write_all(permissions: object, fichier: str, job: str) -> Iterator[Constat]:
    """WF003 : permissions: write-all."""
    if isinstance(permissions, Scalaire) and permissions.texte.strip() == "write-all":
        yield Constat(fichier, permissions.ligne, REGLE_WRITE_ALL, "élevée",
                      f"permissions: write-all donne au {JETON_GITHUB} l'écriture sur tout le dépôt",
                      "déclarer seulement les portées utiles, par exemple « permissions: {contents: read} »",
                      job)


def _regle_permissions(racine: Mappage, jobs: Mappage, fichier: str) -> Iterator[Constat]:
    """WF002 : ni le workflow ni certains jobs ne déclarent leurs permissions."""
    if "permissions" in racine.entrees:
        yield from _regle_write_all(racine.entrees["permissions"], fichier, "")
        return
    sans = [nom for nom, corps in jobs.entrees.items()
            if not (isinstance(corps, Mappage) and "permissions" in corps.entrees)]
    if sans:
        ligne = jobs.lignes_cles.get(sans[0], jobs.ligne)
        yield Constat(fichier, ligne, REGLE_PERMISSIONS, "moyenne",
                      f"aucun bloc permissions pour {', '.join(sans[:5])} : le {JETON_GITHUB} reçoit "
                      "les droits par défaut du dépôt (souvent l'écriture)",
                      "ajouter en tête du workflow « permissions: {contents: read} » puis élargir job par job",
                      ", ".join(sans[:5]))


def analyser_workflow(racine: object, fichier: str) -> list[Constat]:
    """Constats d'un workflow ou d'une action composite ; ValueError si ce n'est ni l'un ni l'autre."""
    if not isinstance(racine, Mappage):
        raise ValueError("pas un workflow : la racine n'est pas un mappage")
    declencheurs = _declencheurs(racine)
    env_global = _variables_secretes(racine.entrees.get("env"))
    constats = list(_tous_secrets(racine, fichier))
    jobs = racine.entrees.get("jobs")
    runs = racine.entrees.get("runs")
    if isinstance(jobs, Mappage):
        constats += _regle_permissions(racine, jobs, fichier)
        for nom, corps in jobs.entrees.items():
            if isinstance(corps, Mappage):
                constats += _regles_job(nom, corps, env_global, (fichier, declencheurs))
    elif isinstance(runs, Mappage) and _texte(runs.entrees.get("using")) == "composite":
        constats += _regles_job("(action composite)", runs, env_global, (fichier, declencheurs))
    else:
        raise ValueError("pas un workflow : ni jobs, ni runs.using: composite")
    return sorted(set(constats), key=lambda c: (c.ligne, c.regle))


# ---------------------------------------------------------------- entrées et sorties

def _fichiers_dossier(dossier: Path) -> list[Path]:
    """Workflows d'un dossier : .github/workflows s'il existe, sinon les *.yml/*.yaml du dossier."""
    cible = dossier / ".github" / "workflows"
    cible = cible if cible.is_dir() else dossier
    return sorted(p for p in cible.iterdir()
                  if p.suffix.lower() in (".yml", ".yaml") and p.is_file() and not p.is_symlink())


def lire_texte(chemin: Path) -> str:
    """Lit un fichier texte borné ; lève ValueError pour un binaire ou un fichier trop gros."""
    if chemin.stat().st_size > TAILLE_MAX:
        raise ValueError(f"plus de {TAILLE_MAX} octets")
    brut = chemin.read_bytes()
    if b"\x00" in brut[:8192]:
        raise ValueError("fichier binaire (octet nul)")
    try:
        return brut.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"pas en utf-8 ({exc.reason})") from exc


def _examiner_un(nom: str, texte: str, avec_pyyaml: bool) -> tuple[list[Constat], str, str]:
    """Rend (constats, refus de lecture, raison d'écart)."""
    try:
        racine = lire_yaml(texte, avec_pyyaml)
    except ValueError as exc:
        return [], str(exc), ""
    try:
        return analyser_workflow(racine, nom), "", ""
    except ValueError as exc:
        return [], "", str(exc)


def _examiner(sources: list[tuple[str, str]], avec_pyyaml: bool) -> dict[str, list]:
    """Analyse chaque (nom, texte) ; sépare examinés, refusés et écartés."""
    resultat: dict[str, list] = {"examines": [], "constats": [], "refus": [], "ignores": []}
    for nom, texte in sources:
        constats, refus, ecart = _examiner_un(nom, texte, avec_pyyaml)
        if refus:
            resultat["refus"].append({"chemin": nom, "raison": refus})
        elif ecart:
            resultat["ignores"].append({"chemin": nom, "raison": ecart})
        else:
            resultat["examines"].append(nom)
            resultat["constats"].extend(constats)
    return resultat


def _rapport(resultat: dict[str, list], seuil: str, moteur: str) -> dict[str, object]:
    """Rapport JSON : constats au-dessus du seuil de gravité."""
    retenus = [c for c in resultat["constats"] if GRAVITES.index(c.gravite) >= GRAVITES.index(seuil)]
    return {
        "denominateur": len(resultat["examines"]),
        "examines": resultat["examines"][:200],
        "moteur": moteur,
        "verdict": "EXPLOITABLE" if retenus else "AUCUN DÉFAUT",
        "gravite_min": seuil,
        "nombre_constats": len(retenus),
        "par_gravite": dict(Counter(c.gravite for c in retenus)),
        "constats": [asdict(c) for c in retenus],
        "refus": resultat["refus"],
        "ignores": resultat["ignores"],
    }


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
    """Liste lisible des constats, avec leur correction."""
    print(f"{rapport['denominateur']} workflow(s) examiné(s) — moteur {rapport['moteur']} — "
          f"{rapport['verdict']} ({rapport['nombre_constats']} constat(s) ≥ {rapport['gravite_min']})")
    for c in rapport["constats"]:
        lieu = " / ".join(x for x in (c["job"], c["etape"]) if x)
        print(f"  {c['fichier']}:{c['ligne']}  {c['regle']} [{c['gravite']}] {c['message']}"
              + (f"  ({lieu})" if lieu else ""))
        print(f"      → {c['correction']}")
    for r in rapport["refus"]:
        print(f"  REFUS de lecture : {r['chemin']} — {r['raison']}")
    for i in rapport["ignores"]:
        print(f"  ignoré : {i['chemin']} — {i['raison']}")


def _parseur() -> argparse.ArgumentParser:
    """Interface en ligne de commande."""
    p = argparse.ArgumentParser(
        description="Audite des workflows GitHub Actions : actions non épinglées, permissions, "
                    "pull_request_target, injection de script, secrets affichés, continue-on-error.",
        epilog=f"Exemple : python {RACINE.name}/auditer_workflow_ci.py .github/workflows --json   "
               "(code 0 : rien ; 1 : défaut ; 2 : entrée invalide ou illisible ; 3 : aucun workflow)")
    p.add_argument("chemins", nargs="*", type=Path,
                   help="fichiers de workflow, ou dossiers (leur .github/workflows s'il existe)")
    p.add_argument("--contenu", help="texte YAML d'un workflow donné en ligne")
    p.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    p.add_argument("--racine", type=Path, default=None,
                   help="base des chemins relatifs (défaut : dossier courant)")
    p.add_argument("--gravite-min", choices=GRAVITES, default="faible",
                   help="ne retenir que les constats de cette gravité ou plus (défaut : faible)")
    p.add_argument("--stdlib", action="store_true",
                   help="utiliser l'analyseur YAML minimal même si PyYAML est installé")
    return p


def _nom_affiche(chemin: Path, base: Path) -> str:
    """Chemin relatif à la base si possible, sinon tel quel."""
    try:
        return str(chemin.relative_to(base))
    except ValueError:
        return str(chemin)


def _sources(o: argparse.Namespace) -> list[tuple[str, str]]:
    """[(nom, texte)] à examiner ; lève ValueError sur un fichier illisible."""
    sources = [("<contenu>", o.contenu)] if o.contenu is not None else []
    for chemin in o.resolus:
        for fichier in _fichiers_dossier(chemin) if chemin.is_dir() else [chemin]:
            nom = _nom_affiche(fichier, o.base)
            try:
                sources.append((nom, lire_texte(fichier)))
            except (OSError, ValueError) as exc:
                raise ValueError(f"{nom} : {exc}") from exc
    return sources


def _usage(o: argparse.Namespace) -> str:
    """Message d'erreur d'usage, ou chaîne vide."""
    if not o.chemins and o.contenu is None:
        return "donner au moins un chemin ou --contenu"
    if not o.base.is_dir():
        return f"--racine n'est pas un dossier : {o.base}"
    manquants = [str(c) for c in o.resolus if not c.exists()]
    return f"chemin introuvable : {manquants[0]}" if manquants else ""


def _code(o: argparse.Namespace, resultat: dict[str, list], rapport: dict[str, object]) -> int:
    """Code de sortie ; écrit sur stderr refus et dénominateur nul."""
    explicites = {_nom_affiche(c, o.base) for c in o.resolus if not c.is_dir()} | {"<contenu>"}
    for ecart in resultat["ignores"]:
        if ecart["chemin"] in explicites:
            print(f"auditer_workflow_ci : {ecart['chemin']} : {ecart['raison']}", file=sys.stderr)
            return CODE_USAGE
    for refus in resultat["refus"]:
        print(f"auditer_workflow_ci : lecture refusée — {refus['chemin']} : {refus['raison']}",
              file=sys.stderr)
    if resultat["refus"]:
        return CODE_USAGE
    if not resultat["examines"]:
        print("auditer_workflow_ci : dénominateur nul — rien à examiner (aucun workflow trouvé).",
              file=sys.stderr)
        return CODE_VIDE
    return CODE_TROUVE if rapport["nombre_constats"] else CODE_RIEN


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée : 0 (rien), 1 (défaut), 2 (usage ou lecture refusée), 3 (aucun workflow)."""
    o = _parseur().parse_args(argv)
    o.base = (o.racine if o.racine is not None else Path.cwd()).resolve()
    o.resolus = [c if c.is_absolute() else o.base / c for c in o.chemins]
    erreur = _usage(o)
    try:
        sources = [] if erreur else _sources(o)
    except ValueError as exc:
        erreur, sources = str(exc), []
    if erreur:
        print(f"auditer_workflow_ci : {erreur}", file=sys.stderr)
        return CODE_USAGE
    avec_pyyaml = yaml is not None and not o.stdlib
    if yaml is None and not o.stdlib:
        print("auditer_workflow_ci : PyYAML absent — analyseur YAML minimal (sous-ensemble "
              "documenté, refus explicite du reste).", file=sys.stderr)
    resultat = _examiner(sources, avec_pyyaml)
    rapport = _rapport(resultat, o.gravite_min, "PyYAML" if avec_pyyaml else "stdlib")
    rapport["contrat"] = extraire_contrat(__doc__ or "")
    if o.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    return _code(o, resultat, rapport)


if __name__ == "__main__":
    raise SystemExit(main())
