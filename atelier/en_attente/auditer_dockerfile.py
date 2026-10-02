"""Auditer un Dockerfile : reproductibilité de la construction et sûreté de l'image produite.

Un Dockerfile peut être soigné et rester exécuté en root : celui de ce dépôt, dont
l'image de base est épinglée par empreinte sha256, n'a ni USER ni HEALTHCHECK, et
`auditer_dockerfile.py Dockerfile` y relève 2 constats. Les analyseurs ne s'accordent
pas non plus : dockerfile-parse 2.0.1 lit les deux lignes du corps d'un heredoc
(« curl ... | sh » puis le délimiteur) comme deux instructions inventées, si bien qu'un
téléchargement exécuté dans un heredoc lui échappe ; cet outil lit les heredocs.

QUESTION
    Ce Dockerfile est-il reproductible (image de base épinglée, paquets maîtrisés) et
    sûr (pas de root, pas de secret dans l'image, pas de script téléchargé et exécuté
    à l'aveugle) ?
MESURE
    Analyse statique, sans construire ni exécuter : découpage en instructions avec
    continuations de ligne (caractère d'échappement de la directive escape), lignes de
    commentaire internes, heredocs (<<délimiteur, <<-délimiteur, délimiteur cité,
    plusieurs par instruction) ; suivi des étapes (FROM ... AS, héritage d'une étape par une autre)
    et des ARG globaux pour résoudre l'image de base. Règles : DF001 image sans
    empreinte, DF002 étiquette latest ou absente, DF003 utilisateur final root ou
    absent, DF004 ADD d'une URL sans --checksum, DF005 ADD local non archive, DF006
    apt sans --no-install-recommends, DF007 listes apt non nettoyées, DF008 apt-get
    update isolé, DF009 téléchargement redirigé vers un shell, DF010 nom ENV ou ARG
    secret (password, token, secret, key...), DF011 COPY . avant l'installation des
    dépendances, DF012 HEALTHCHECK absent, DF013 plusieurs CMD ou ENTRYPOINT dans une
    étape, DF014 vérification TLS désactivée, DF015 syntaxe (heredoc non terminé,
    instruction inconnue). Chaque constat donne règle, ligne, gravité, correction. Si
    dockerfile-parse est installé, son découpage est confronté au nôtre (fichiers
    sans heredoc) et les divergences sont rendues.
HYPOTHÈSES
    Le fichier suit la syntaxe Dockerfile de BuildKit ; les commandes RUN sont du shell
    posix lisible ; le dernier stade du fichier est l'image livrée.
LIMITES
    Ne connaît pas l'image de base : un USER ou un HEALTHCHECK hérité d'elle est
    invisible ; ne résout que les ARG globaux à valeur par défaut ; n'ouvre pas les
    scripts copiés puis exécutés (RUN ./install.sh) ; ne lit pas .dockerignore ;
    l'analyse du shell est lexicale (une commande construite dans une variable
    échappe).
CONTRE-EXEMPLES
    Faux positif constaté : « ENV GPG_KEY=<empreinte hexadécimale de 40 caractères> »,
    tel que l'écrivent les images officielles python, est signalé DF010 alors que
    c'est l'empreinte publique d'une clé de signature, pas un secret. Faux négatif
    constaté : « RUN ./installer.sh » après « COPY installer.sh . » n'est pas signalé,
    même si le script fait lui-même « curl | sh ».
INVOCATION
    {outil} --contenu "FROM python:3.14-slim" --json
DOMAINE
    Dockerfile et Containerfile de BuildKit (Docker, Podman) relus avant une
    construction en intégration continue ou une mise en production.
"""

from __future__ import annotations

import argparse
import io
import json
import os
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
    from dockerfile_parse import DockerfileParser
except ImportError:
    DockerfileParser = None

RACINE = Path(__file__).resolve().parent

__all__ = ["analyser_dockerfile", "decouper_instructions", "main"]

TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_INVOCATION = "INVOCATION"
TITRES_CONTRAT = ("QUESTION", "MESURE", TITRE_HYPOTHESES, "LIMITES",
                  "CONTRE-EXEMPLES", TITRE_INVOCATION, "DOMAINE")
INSTR_FROM = "FROM"
INSTR_COPY = "COPY"
INSTR_USER = "USER"
REGLE_EMPREINTE = "DF001"
REGLE_ETIQUETTE = "DF002"
REGLE_ROOT = "DF003"
REGLE_ADD_URL = "DF004"
REGLE_ADD_LOCAL = "DF005"
REGLE_APT_RECOMMANDES = "DF006"
REGLE_APT_LISTES = "DF007"
REGLE_APT_UPDATE = "DF008"
REGLE_PIPE_SHELL = "DF009"
REGLE_SECRET = "DF010"
REGLE_CACHE = "DF011"
REGLE_HEALTHCHECK = "DF012"
REGLE_CMD = "DF013"
REGLE_TLS = "DF014"
REGLE_SYNTAXE = "DF015"
INSTR_HEALTHCHECK = "HEALTHCHECK"
INSTR_ENTRYPOINT = "ENTRYPOINT"
INSTRUCTIONS = frozenset({INSTR_FROM, "RUN", "CMD", "LABEL", "MAINTAINER", "EXPOSE", "ENV", "ADD",
                          INSTR_COPY, INSTR_ENTRYPOINT, "VOLUME", INSTR_USER, "WORKDIR", "ARG",
                          "ONBUILD", "STOPSIGNAL", INSTR_HEALTHCHECK, "SHELL"})
GRAVITES = ("info", "faible", "moyenne", "élevée", "critique")

CODE_RIEN = 0
CODE_TROUVE = 1
CODE_USAGE = 2
CODE_VIDE = 3
TAILLE_MAX = 5_000_000
DOSSIERS_SAUTES = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv",
                             "venv", ".tox"})
ARCHIVES = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".tar.zst")
UTILISATEURS_ROOT = frozenset({"root", "0", "root:root", "0:0", "root:0", "0:root"})
MOTS_SECRETS = frozenset({"PASSWORD", "PASSWD", "PASS", "TOKEN", "SECRET", "KEY", "APIKEY",
                          "CREDENTIAL", "CREDENTIALS", "PRIVATE"})
SUFFIXES_ANODINS = frozenset({"FILE", "PATH", "DIR", "URL", "NAME", "ID_FILE"})

MOTIF_DIRECTIVE = re.compile(r"^#\s*([A-Za-z]+)\s*=\s*(\S+)\s*$")
MOTIF_HEREDOC = re.compile(r"(?<!<)<<(-?)([\"']?)([A-Za-z_][A-Za-z0-9_]*)\2")
MOTIF_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::?[-+]([^}]*))?\}|\$([A-Za-z_][A-Za-z0-9_]*)")
MOTIF_SEPARATEURS = re.compile(r"&&|\|\||;|\n|(?<!\|)\|(?!\|)")
MOTIF_APT_INSTALL = re.compile(r"\bapt(?:-get)?\s+(?:-\S+\s+)*install\b")
MOTIF_APT_UPDATE = re.compile(r"\bapt(?:-get)?\s+(?:-\S+\s+)*update\b")
MOTIF_APT_NETTOYAGE = re.compile(r"\brm\s+(?:-\S+\s+)*[^&;|]*?/var/lib/apt/lists")
MOTIF_CACHE_APT = re.compile(r"--mount=[^\s]*type=cache[^\s]*target=/var/(?:lib|cache)/apt")
MOTIF_PIPE_SHELL = re.compile(
    r"\b(?:curl|wget|fetch)\b[^|;&\n]*\|\s*(?:sudo\s+(?:-\S+\s+)*)?(?:env\s+\S+\s+)*"
    r"(?:ba|z|k|da|fi)?sh\b|\b(?:curl|wget)\b[^|;&\n]*\|\s*(?:sudo\s+)?python[0-9.]*\b"
    r"|\b(?:ba)?sh\s+(?:-\S+\s+)*<\(\s*(?:curl|wget)\b|\bsh\s+-c\s+[\"']?\$\(\s*(?:curl|wget)\b")
MOTIF_TLS_DESACTIVE = re.compile(
    r"\bcurl\b[^|;&\n]*\s(?:-k|--insecure)\b|\bwget\b[^|;&\n]*--no-check-certificate"
    r"|\bpip3?\b[^|;&\n]*--trusted-host\b|\bgit\s+config\b[^|;&\n]*http\.sslVerify\s+false")
MOTIF_DEPENDANCES = re.compile(
    r"\b(?:pip3?|uv\s+pip|python3?\s+-m\s+pip)\s+install\b|\buv\s+sync\b|\bpoetry\s+install\b"
    r"|\bpipenv\s+install\b|\bnpm\s+(?:ci|install|i)\b|\byarn(?:\s+install)?\s*(?:$|&&|;|--)"
    r"|\bpnpm\s+(?:install|i)\b|\bbundle\s+install\b|\bcomposer\s+install\b"
    r"|\bgo\s+mod\s+download\b|\bcargo\s+(?:build|fetch)\b|\bmvn\b|\bgradle\b")


@dataclass(frozen=True)
class Instruction:
    """Une instruction logique : continuations jointes, commentaires internes ôtés."""

    nom: str
    arguments: str
    ligne: int
    fin: int
    heredocs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Constat:
    """Un défaut : règle, ligne, gravité, correction."""

    fichier: str
    ligne: int
    regle: str
    gravite: str
    message: str
    correction: str


@dataclass
class Etape:
    """Une étape de construction (FROM ... jusqu'au FROM suivant)."""

    indice: int
    alias: str
    base: str
    ligne: int
    parent: int | None
    instructions: list[Instruction] = field(default_factory=list)


def _directive_escape(lignes: list[str]) -> str:
    """Caractère d'échappement fixé par la directive « # escape= » en tête, sinon antislash."""
    for ligne in lignes:
        m = MOTIF_DIRECTIVE.match(ligne.strip())
        if not m:
            return "\\"
        if m.group(1).lower() == "escape" and m.group(2) in ("\\", "`"):
            return m.group(2)
    return "\\"


def _lire_heredocs(entete: str, lignes: list[str], i: int) -> tuple[tuple[str, ...], int, bool]:
    """Consomme les corps des heredocs annoncés ; rend (corps, indice suivant, terminé)."""
    corps: list[str] = []
    for m in MOTIF_HEREDOC.finditer(entete):
        retrait, delimiteur = m.group(1) == "-", m.group(3)
        contenu: list[str] = []
        while i < len(lignes):
            courante = lignes[i].lstrip("\t") if retrait else lignes[i]
            i += 1
            if courante.rstrip("\r") == delimiteur:
                break
            contenu.append(courante)
        else:
            corps.append("\n".join(contenu))
            return tuple(corps), i, False
        corps.append("\n".join(contenu))
    return tuple(corps), i, True


def _joindre_continuations(lignes: list[str], i: int, echappement: str) -> tuple[str, int]:
    """Joint une instruction écrite sur plusieurs lignes ; rend (texte, indice suivant)."""
    morceaux: list[str] = []
    while i < len(lignes):
        ligne = lignes[i].rstrip()
        i += 1
        if morceaux and (not ligne.strip() or ligne.lstrip().startswith("#")):
            continue
        if ligne.endswith(echappement):
            morceaux.append(ligne[:-1])
            continue
        morceaux.append(ligne)
        break
    return " ".join(m.strip() for m in morceaux), i


def decouper_instructions(texte: str) -> tuple[list[Instruction], list[tuple[int, str]]]:
    """Découpe un Dockerfile ; rend (instructions, problèmes de syntaxe (ligne, message))."""
    lignes = texte.splitlines()
    echappement = _directive_escape(lignes)
    instructions: list[Instruction] = []
    problemes: list[tuple[int, str]] = []
    i = 0
    while i < len(lignes):
        if not lignes[i].strip() or lignes[i].lstrip().startswith("#"):
            i += 1
            continue
        debut = i
        logique, i = _joindre_continuations(lignes, i, echappement)
        nom, _, arguments = logique.partition(" ")
        heredocs: tuple[str, ...] = ()
        if nom.upper() in ("RUN", INSTR_COPY, "ADD") and MOTIF_HEREDOC.search(arguments):
            heredocs, i, termine = _lire_heredocs(arguments, lignes, i)
            if not termine:
                problemes.append((debut + 1, "heredoc non terminé avant la fin du fichier"))
        if nom.upper() not in INSTRUCTIONS:
            problemes.append((debut + 1, f"instruction inconnue « {nom[:40]} »"))
        instructions.append(Instruction(nom.upper(), arguments.strip(), debut + 1, i, heredocs))
    return instructions, problemes


def _sans_options(arguments: str) -> list[str]:
    """Jetons d'une instruction, options --x=y retirées."""
    return [j for j in arguments.split() if not j.startswith("--")]


def _substituer(texte: str, valeurs: dict[str, str]) -> str:
    """Remplace $X, ${X}, ${X:-d} par les valeurs connues (le défaut sinon)."""
    def remplacer(m: re.Match[str]) -> str:
        nom = m.group(1) or m.group(3)
        if nom in valeurs:
            return valeurs[nom]
        return m.group(2) if m.group(2) is not None else m.group(0)
    return MOTIF_VARIABLE.sub(remplacer, texte)


def _args_globaux(instructions: list[Instruction]) -> dict[str, str]:
    """ARG déclarés avant le premier FROM, avec leur valeur par défaut."""
    valeurs: dict[str, str] = {}
    for ins in instructions:
        if ins.nom == INSTR_FROM:
            break
        if ins.nom == "ARG":
            for jeton in ins.arguments.split():
                nom, egal, valeur = jeton.partition("=")
                if egal:
                    valeurs[nom] = valeur.strip("\"'")
    return valeurs


def _etapes(instructions: list[Instruction]) -> list[Etape]:
    """Regroupe les instructions par étape et relie les étapes héritées."""
    globaux = _args_globaux(instructions)
    etapes: list[Etape] = []
    for ins in instructions:
        if ins.nom == INSTR_FROM:
            jetons = _sans_options(ins.arguments)
            base = _substituer(jetons[0], globaux) if jetons else ""
            alias = jetons[2].lower() if len(jetons) >= 3 and jetons[1].lower() == "as" else ""
            parent = next((e.indice for e in etapes if e.alias and e.alias == base.lower()), None)
            etapes.append(Etape(len(etapes), alias, base, ins.ligne, parent))
        elif etapes:
            etapes[-1].instructions.append(ins)
    return etapes


def decomposer_image(image: str) -> tuple[str, str, str]:
    """Rend (nom, étiquette, empreinte) d'une référence d'image."""
    nom, _, empreinte = image.partition("@")
    dernier = nom.rsplit("/", 1)[-1]
    etiquette = dernier.rpartition(":")[2] if ":" in dernier else ""
    if etiquette:
        nom = nom[: -len(etiquette) - 1]
    return nom, etiquette, empreinte


def _regles_from(etape: Etape, fichier: str) -> Iterator[Constat]:
    """DF001 et DF002 : épinglage de l'image de base."""
    if etape.parent is not None or etape.base.lower() == "scratch" or not etape.base:
        return
    if "$" in etape.base:
        yield Constat(fichier, etape.ligne, REGLE_EMPREINTE, "faible",
                      f"image de base déterminée par une variable non résolue ({etape.base})",
                      "donner une valeur par défaut épinglée à l'ARG, ou écrire l'image en clair")
        return
    nom, etiquette, empreinte = decomposer_image(etape.base)
    if empreinte.startswith("sha256:"):
        return
    if etiquette in ("", "latest"):
        yield Constat(fichier, etape.ligne, REGLE_ETIQUETTE, "élevée",
                      f"image « {etape.base} » sans étiquette précise (latest implicite ou explicite)",
                      f"FROM {nom}:<version>@sha256:<empreinte> (docker buildx imagetools inspect {nom}:<version>)")
    else:
        yield Constat(fichier, etape.ligne, REGLE_EMPREINTE, "moyenne",
                      f"image « {etape.base} » épinglée par étiquette mais pas par empreinte",
                      f"FROM {etape.base}@sha256:<empreinte> (docker buildx imagetools inspect {etape.base})")


def _texte_run(ins: Instruction) -> str:
    """Texte shell complet d'un RUN : ligne logique et corps des heredocs."""
    return "\n".join((ins.arguments,) + ins.heredocs)


def _segments(script: str) -> list[str]:
    """Commandes simples d'un script, découpées sur && || ; | et retours à la ligne."""
    return [s.strip() for s in MOTIF_SEPARATEURS.split(script) if s.strip()]


def _regles_apt(ins: Instruction, fichier: str) -> Iterator[Constat]:
    """DF006, DF007, DF008 : usage d'apt dans un RUN."""
    script = _texte_run(ins)
    installe = MOTIF_APT_INSTALL.search(script) is not None
    if any(MOTIF_APT_INSTALL.search(s) and "--no-install-recommends" not in s for s in _segments(script)):
        yield Constat(fichier, ins.ligne, REGLE_APT_RECOMMANDES, "faible",
                      "apt install sans --no-install-recommends : paquets superflus, image plus grosse",
                      "apt-get install -y --no-install-recommends <paquets>")
    if installe and not MOTIF_APT_NETTOYAGE.search(script) and not MOTIF_CACHE_APT.search(ins.arguments):
        yield Constat(fichier, ins.ligne, REGLE_APT_LISTES, "faible",
                      "listes apt laissées dans la couche (/var/lib/apt/lists)",
                      "terminer le même RUN par « && rm -rf /var/lib/apt/lists/* »")
    if MOTIF_APT_UPDATE.search(script) and not installe:
        yield Constat(fichier, ins.ligne, REGLE_APT_UPDATE, "moyenne",
                      "apt-get update seul dans son RUN : la couche en cache rend les listes périmées",
                      "réunir « apt-get update && apt-get install ... » dans un seul RUN")


def _regles_run(ins: Instruction, fichier: str) -> Iterator[Constat]:
    """DF009 et DF014 : exécution aveugle d'un téléchargement, TLS désactivé."""
    script = _texte_run(ins)
    if MOTIF_PIPE_SHELL.search(script):
        yield Constat(fichier, ins.ligne, REGLE_PIPE_SHELL, "élevée",
                      "script téléchargé puis exécuté sans vérification (curl | sh)",
                      "télécharger dans un fichier, vérifier son empreinte (sha256sum -c), puis l'exécuter")
    if MOTIF_TLS_DESACTIVE.search(script):
        yield Constat(fichier, ins.ligne, REGLE_TLS, "moyenne",
                      "vérification TLS désactivée (curl -k, --no-check-certificate, --trusted-host...)",
                      "retirer l'option et installer les certificats d'autorité nécessaires")
    yield from _regles_apt(ins, fichier)


def _regles_add(ins: Instruction, fichier: str) -> Iterator[Constat]:
    """DF004 et DF005 : ADD distant sans empreinte, ADD local au lieu de COPY."""
    jetons = _sans_options(ins.arguments)
    sources = jetons[:-1] if len(jetons) > 1 else []
    for source in sources:
        distant = re.match(r"(?i)(https?://|git@|ssh://)", source) is not None
        if distant and "--checksum=" not in ins.arguments:
            yield Constat(fichier, ins.ligne, REGLE_ADD_URL, "moyenne",
                          f"ADD télécharge {source[:80]} sans vérifier d'empreinte",
                          "ADD --checksum=sha256:<empreinte> <url> <dest>, ou RUN curl + sha256sum -c")
        elif not distant and not ins.heredocs and not source.lower().endswith(ARCHIVES):
            yield Constat(fichier, ins.ligne, REGLE_ADD_LOCAL, "faible",
                          f"ADD d'une source locale non archive ({source[:80]})",
                          "utiliser COPY, dont l'effet est explicite")


def _noms_variables(ins: Instruction) -> list[tuple[str, str]]:
    """(nom, valeur) déclarés par ENV ou ARG, formes « K=V » et « K V »."""
    texte = ins.arguments
    if ins.nom == "ENV" and "=" not in texte.split(" ", 1)[0]:
        nom, _, valeur = texte.partition(" ")
        return [(nom, valeur.strip())]
    paires = re.findall(r"([A-Za-z_][A-Za-z0-9_.-]*)(?:=(\"[^\"]*\"|'[^']*'|\S*))?", texte)
    return [(n, v.strip("\"'")) for n, v in paires]


def nom_secret(nom: str) -> bool:
    """Vrai si un mot du nom évoque un secret (PASSWORD, TOKEN, SECRET, KEY...)."""
    mots = [m for m in re.split(r"[_.\-]+", nom.upper()) if m]
    if not mots or mots[-1] in SUFFIXES_ANODINS or "PUBLIC" in mots:
        return False
    return any(m in MOTS_SECRETS or m.endswith(("PASSWORD", "TOKEN", "SECRET")) for m in mots)


def _regles_env_arg(ins: Instruction, fichier: str) -> Iterator[Constat]:
    """DF010 : un secret passé par ENV (persiste dans l'image) ou ARG (visible dans l'historique)."""
    for nom, valeur in _noms_variables(ins):
        if not nom_secret(nom):
            continue
        litteral = bool(valeur) and not valeur.startswith("$")
        gravite = "élevée" if ins.nom == "ENV" and litteral else "moyenne"
        lieu = "dans la configuration de l'image" if ins.nom == "ENV" else "dans docker history"
        yield Constat(fichier, ins.ligne, REGLE_SECRET, gravite,
                      f"{ins.nom} {nom} : un secret ainsi passé reste lisible {lieu}",
                      f"RUN --mount=type=secret,id={nom.lower()} ... (BuildKit), jamais ARG ni ENV")


def _regles_etape(etape: Etape, fichier: str) -> Iterator[Constat]:
    """Règles instruction par instruction, puis DF011 et DF013 pour l'étape."""
    yield from _regles_from(etape, fichier)
    for ins in etape.instructions:
        if ins.nom == "RUN":
            yield from _regles_run(ins, fichier)
        elif ins.nom == "ADD":
            yield from _regles_add(ins, fichier)
        elif ins.nom in ("ENV", "ARG"):
            yield from _regles_env_arg(ins, fichier)
    yield from _regle_copie_globale(etape, fichier)
    yield from _regle_cmd_multiples(etape, fichier)


def _copie_globale(ins: Instruction) -> bool:
    """COPY ou ADD dont une source est le contexte entier (« . » ou « ./ »)."""
    jetons = _sans_options(ins.arguments)
    return ins.nom in (INSTR_COPY, "ADD") and any(j in (".", "./") for j in jetons[:-1])


def _regle_copie_globale(etape: Etape, fichier: str) -> Iterator[Constat]:
    """DF011 : COPY . avant la première installation de dépendances de l'étape."""
    copie: Instruction | None = None
    for ins in etape.instructions:
        if copie is None and _copie_globale(ins):
            copie = ins
        elif ins.nom == "RUN" and MOTIF_DEPENDANCES.search(_texte_run(ins)):
            if copie is not None:
                yield Constat(fichier, copie.ligne, REGLE_CACHE, "faible",
                              f"COPY . précède l'installation des dépendances (ligne {ins.ligne}) : "
                              "tout changement de code invalide le cache des dépendances",
                              "copier d'abord les manifestes (requirements.txt, package*.json...), "
                              "installer, puis COPY . ")
            return


def _regle_cmd_multiples(etape: Etape, fichier: str) -> Iterator[Constat]:
    """DF013 : seul le dernier CMD (ou ENTRYPOINT) d'une étape compte."""
    for nom in ("CMD", INSTR_ENTRYPOINT):
        lignes = [i.ligne for i in etape.instructions if i.nom == nom]
        for ligne in lignes[:-1]:
            yield Constat(fichier, ligne, REGLE_CMD, "moyenne",
                          f"{nom} ligne {ligne} écrasé par celui de la ligne {lignes[-1]}",
                          f"ne garder qu'un {nom} par étape")


def _chaine(etapes: list[Etape], derniere: Etape) -> list[Etape]:
    """L'étape finale et celles dont elle hérite, de la plus ancienne à la finale."""
    chaine = [derniere]
    while chaine[0].parent is not None and len(chaine) <= len(etapes):
        chaine.insert(0, etapes[chaine[0].parent])
    return chaine


def _regles_finales(etapes: list[Etape], fichier: str) -> Iterator[Constat]:
    """DF003 et DF012 sur l'image livrée (dernière étape et ses ancêtres internes)."""
    chaine = _chaine(etapes, etapes[-1])
    instructions = [i for e in chaine for i in e.instructions]
    users = [i for i in instructions if i.nom == INSTR_USER]
    if not users:
        yield Constat(fichier, etapes[-1].ligne, REGLE_ROOT, "moyenne",
                      "aucun USER dans l'image livrée : le conteneur tourne en root, sauf si "
                      "l'image de base fixe un autre utilisateur",
                      "créer un utilisateur (useradd -r -u 10001 app) puis « USER app » en fin de fichier")
    elif users[-1].arguments.split()[0].lower() in UTILISATEURS_ROOT:
        yield Constat(fichier, users[-1].ligne, REGLE_ROOT, "élevée",
                      "le dernier USER de l'image livrée est root",
                      "terminer par « USER <utilisateur non privilégié> »")
    if not any(i.nom == INSTR_HEALTHCHECK for i in instructions):
        yield Constat(fichier, etapes[-1].ligne, REGLE_HEALTHCHECK, "faible",
                      "aucun HEALTHCHECK : l'orchestrateur ne sait pas si le service répond",
                      "HEALTHCHECK CMD <commande de sonde>, ou HEALTHCHECK NONE si c'est voulu")


def analyser_dockerfile(texte: str, fichier: str) -> tuple[list[Constat], list[Instruction]]:
    """Tous les constats d'un Dockerfile ; lève ValueError s'il n'a aucun FROM."""
    instructions, problemes = decouper_instructions(texte)
    etapes = _etapes(instructions)
    if not etapes:
        raise ValueError("aucune instruction FROM : ce n'est pas un Dockerfile")
    constats = [Constat(fichier, ligne, REGLE_SYNTAXE, "moyenne", message,
                        "corriger la syntaxe (BuildKit refusera ou lira autre chose)")
                for ligne, message in problemes]
    for etape in etapes:
        constats.extend(_regles_etape(etape, fichier))
    constats.extend(_regles_finales(etapes, fichier))
    return sorted(constats, key=lambda c: (c.ligne, c.regle)), instructions


def confronter_bibliotheque(texte: str, instructions: list[Instruction]) -> dict[str, object]:
    """Compare notre découpage à celui de dockerfile-parse (sans heredoc seulement)."""
    if any(i.heredocs for i in instructions):
        return {"compare": False, "raison": "heredoc présent : dockerfile-parse ne les lit pas"}
    analyseur = DockerfileParser(fileobj=io.BytesIO(texte.encode("utf-8")))
    leurs = [(s["instruction"].upper(), s["startline"] + 1) for s in analyseur.structure
             if s["instruction"].upper() != "COMMENT"]
    notres = [(i.nom, i.ligne) for i in instructions]
    divergences = sorted(set(leurs) ^ set(notres), key=lambda p: p[1])
    return {"compare": True, "instructions_stdlib": len(notres), "instructions_bibliotheque": len(leurs),
            "divergences": [{"instruction": n, "ligne": l} for n, l in divergences[:50]]}


def _est_dockerfile(nom: str) -> bool:
    """Dockerfile*, Containerfile*, *.dockerfile (sans les .dockerignore)."""
    bas = nom.lower()
    if bas.endswith(".dockerignore"):
        return False
    return bas.startswith(("dockerfile", "containerfile")) or bas.endswith(".dockerfile")


def _fichiers(chemins: list[Path], ignores: list[dict[str, str]]) -> Iterator[Path]:
    """Fichiers donnés, et Dockerfile trouvés sous les dossiers donnés (liens non suivis)."""
    for chemin in chemins:
        if not chemin.is_dir():
            yield chemin
            continue
        for courant, sous, noms in os.walk(chemin):
            sous[:] = sorted(n for n in sous if n not in DOSSIERS_SAUTES
                             and not (Path(courant) / n).is_symlink())
            for nom in sorted(noms):
                cible = Path(courant) / nom
                if _est_dockerfile(nom) and not cible.is_symlink():
                    yield cible
                elif _est_dockerfile(nom):
                    ignores.append({"chemin": cible.name, "raison": "lien symbolique non suivi"})


def lire_texte(chemin: Path) -> str:
    """Lit un fichier texte borné ; lève ValueError pour un binaire ou un fichier trop gros."""
    if chemin.stat().st_size > TAILLE_MAX:
        raise ValueError(f"plus de {TAILLE_MAX} octets")
    brut = chemin.read_bytes()
    if b"\x00" in brut[:8192]:
        raise ValueError("fichier binaire (octet nul)")
    return brut.decode("utf-8", errors="replace")


def _examiner(sources: list[tuple[str, str]], avec_tiers: bool) -> dict[str, object]:
    """Analyse chaque (nom, texte) ; les non-Dockerfile sont écartés."""
    examines: list[str] = []
    ignores: list[dict[str, str]] = []
    constats: list[Constat] = []
    comparaisons: dict[str, object] = {}
    for nom, texte in sources:
        try:
            trouves, instructions = analyser_dockerfile(texte, nom)
        except ValueError as exc:
            ignores.append({"chemin": nom, "raison": str(exc)})
            continue
        examines.append(nom)
        constats.extend(trouves)
        if avec_tiers:
            comparaisons[nom] = confronter_bibliotheque(texte, instructions)
    return {"examines": examines, "ignores": ignores, "constats": constats,
            "comparaisons": comparaisons}


def _rapport(resultat: dict[str, object], seuil: str, avec_tiers: bool) -> dict[str, object]:
    """Rapport JSON : constats au-dessus du seuil de gravité."""
    rang = GRAVITES.index(seuil)
    retenus = [c for c in resultat["constats"] if GRAVITES.index(c.gravite) >= rang]
    examines = resultat["examines"]
    rapport: dict[str, object] = {
        "denominateur": len(examines),
        "examines": examines[:200],
        "moteur": "dockerfile-parse" if avec_tiers else "stdlib",
        "verdict": "DÉFAUTS" if retenus else "AUCUN DÉFAUT",
        "gravite_min": seuil,
        "nombre_constats": len(retenus),
        "par_gravite": dict(Counter(c.gravite for c in retenus)),
        "constats": [asdict(c) for c in retenus],
        "ignores": resultat["ignores"],
    }
    if avec_tiers:
        rapport["comparaison_moteurs"] = resultat["comparaisons"]
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
    """Liste lisible des constats, avec leur correction."""
    print(f"{rapport['denominateur']} Dockerfile examiné(s) — moteur {rapport['moteur']} — "
          f"{rapport['verdict']} ({rapport['nombre_constats']} constat(s) ≥ {rapport['gravite_min']})")
    for c in rapport["constats"]:
        print(f"  {c['fichier']}:{c['ligne']}  {c['regle']} [{c['gravite']}] {c['message']}")
        print(f"      → {c['correction']}")
    for i in rapport["ignores"]:
        print(f"  ignoré : {i['chemin']} — {i['raison']}")


def _parseur() -> argparse.ArgumentParser:
    """Interface en ligne de commande."""
    p = argparse.ArgumentParser(
        description="Audite des Dockerfile (reproductibilité et sûreté) : image non épinglée, "
                    "root, ADD d'URL, apt, curl | sh, secrets dans ENV/ARG, cache, HEALTHCHECK.",
        epilog=f"Exemple : python {RACINE.name}/auditer_dockerfile.py Dockerfile --json   "
               "(code 0 : rien ; 1 : défaut ; 2 : entrée invalide ; 3 : aucun Dockerfile)")
    p.add_argument("chemins", nargs="*", type=Path,
                   help="Dockerfile, ou dossiers où chercher Dockerfile*, Containerfile*, *.dockerfile")
    p.add_argument("--contenu", help="texte d'un Dockerfile donné en ligne (au lieu d'un fichier)")
    p.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    p.add_argument("--racine", type=Path, default=None,
                   help="base des chemins relatifs (défaut : dossier courant)")
    p.add_argument("--gravite-min", choices=GRAVITES, default="faible",
                   help="ne retenir que les constats de cette gravité ou plus (défaut : faible)")
    p.add_argument("--stdlib", action="store_true",
                   help="ne pas confronter à dockerfile-parse même s'il est installé")
    return p


def _nom_affiche(chemin: Path, base: Path) -> str:
    """Chemin relatif à la base si possible, sinon tel quel."""
    try:
        return str(chemin.relative_to(base))
    except ValueError:
        return str(chemin)


def _sources(o: argparse.Namespace, ignores: list[dict[str, str]]) -> list[tuple[str, str]]:
    """Rend [(nom, texte)] ; lève ValueError si un fichier donné explicitement est illisible."""
    sources: list[tuple[str, str]] = []
    if o.contenu is not None:
        sources.append(("<contenu>", o.contenu))
    for chemin in _fichiers(o.resolus, ignores):
        nom = _nom_affiche(chemin, o.base)
        try:
            sources.append((nom, lire_texte(chemin)))
        except (OSError, ValueError) as exc:
            if chemin in o.resolus:
                raise ValueError(f"{nom} : {exc}") from exc
            ignores.append({"chemin": nom, "raison": str(exc)})
    return sources


def _usage(o: argparse.Namespace) -> str:
    """Message d'erreur d'usage, ou chaîne vide."""
    if not o.chemins and o.contenu is None:
        return "donner au moins un chemin ou --contenu"
    if not o.base.is_dir():
        return f"--racine n'est pas un dossier : {o.base}"
    manquants = [str(c) for c in o.resolus if not c.exists()]
    return f"chemin introuvable : {manquants[0]}" if manquants else ""


def _refus_explicite(o: argparse.Namespace, ignores: list[dict[str, str]]) -> str:
    """Un fichier ou un --contenu donné explicitement qui n'est pas un Dockerfile est une erreur."""
    explicites = {_nom_affiche(c, o.base) for c in o.resolus if not c.is_dir()} | {"<contenu>"}
    for ignore in ignores:
        if ignore["chemin"] in explicites:
            return f"{ignore['chemin']} : {ignore['raison']}"
    return ""


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée : 0 (rien), 1 (défaut), 2 (usage), 3 (aucun Dockerfile)."""
    o = _parseur().parse_args(argv)
    o.base = (o.racine if o.racine is not None else Path.cwd()).resolve()
    o.resolus = [c if c.is_absolute() else o.base / c for c in o.chemins]
    ignores: list[dict[str, str]] = []
    sources: list[tuple[str, str]] = []
    erreur = _usage(o)
    try:
        sources = [] if erreur else _sources(o, ignores)
    except ValueError as exc:
        erreur = str(exc)
    avec_tiers = DockerfileParser is not None and not o.stdlib
    resultat = _examiner(sources, avec_tiers)
    resultat["ignores"] = ignores + resultat["ignores"]
    erreur = erreur or _refus_explicite(o, resultat["ignores"])
    if erreur:
        print(f"auditer_dockerfile : {erreur}", file=sys.stderr)
        return CODE_USAGE
    if DockerfileParser is None and not o.stdlib:
        print("auditer_dockerfile : dockerfile-parse absent — analyseur stdlib seul, sans "
              "confrontation des découpages.", file=sys.stderr)
    rapport = _rapport(resultat, o.gravite_min, avec_tiers)
    rapport["contrat"] = extraire_contrat(__doc__ or "")
    if not resultat["examines"]:
        print("auditer_dockerfile : dénominateur nul — rien à examiner (aucun Dockerfile trouvé).",
              file=sys.stderr)
    if o.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    if not resultat["examines"]:
        return CODE_VIDE
    return CODE_TROUVE if rapport["nombre_constats"] else CODE_RIEN


if __name__ == "__main__":
    raise SystemExit(main())
