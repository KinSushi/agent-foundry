r"""Dit si des messages de commit respectent Conventional Commits 1.0 et quelle version ils impliquent.

Même l'outil de référence s'écarte de la spécification : mesuré le 2026-10-02 avec commitizen
4.19.0, « Feat: x » est refusé alors que la règle 15 rend le type insensible à la casse,
« refactor: » et « perf: » montent le correctif alors que la spécification ne leur donne aucun
effet, et « chore: x » dont le corps contient une ligne « feat: … » monte la mineure (chaque
ligne est lue comme un en-tête). Sur les 7 commits de ce dépôt, aucun n'est conforme.

QUESTION
    Ces messages de commit respectent-ils Conventional Commits 1.0, et quelle version SemVer
    suivante impliquent-ils ?
MESURE
    Chaque message est confronté aux seize règles de la spécification 1.0 : en-tête
    « type(portée)!: description » (type et portée insensibles à la casse, portée non vide,
    deux-points suivi d'une espace, description non vide), ligne vide avant le corps, pieds
    de page « Jeton: valeur » ou « Jeton #valeur » (jetons sans espace, sauf BREAKING CHANGE),
    rupture signalée par « ! » ou par un pied BREAKING CHANGE / BREAKING-CHANGE en
    majuscules. Chaque écart est nommé ; les pièges courants (« breaking change: » en
    minuscules, « feat:x » sans espace, portée vide) sont signalés. La montée de version
    suit la spécification : rupture → majeure, feat → mineure, fix → corrective, autres types
    → aucune (--patch-aussi pour étendre) ; elle part de --depuis (pré-versions comprises,
    règle d'incrément de npm). Si commitizen est importable, chaque message est aussi jugé
    par son motif et la montée recalculée par commitizen ; les désaccords sont listés.
HYPOTHÈSES
    Entrée : un fichier de messages (séparés par NUL comme `git log --format=%B%x00`, ou une
    sortie `git log` par défaut, ou un sujet par ligne, ou un séparateur donné), ou des
    messages en ligne (--message). Les messages sont en UTF-8. Les commits de fusion, de
    retour automatique et fixup!/squash! sont écartés (comme commitlint) sauf --strict.
LIMITES
    Ne lit pas le dépôt git (voir lire_historique_git) : l'ordre et le choix des commits sont
    ceux de l'entrée. La spécification n'impose aucune liste de types : tout mot est accepté,
    sauf --types. Les pieds sont cherchés dans le dernier paragraphe (convention des trailers
    git), ou dès le premier BREAKING CHANGE ; une ligne « Note: … » d'un corps finissant le
    message est donc lue comme un pied. Pas de lecture de configuration commitlint.
CONTRE-EXEMPLES
    Constaté : un fichier qui contient UN message « docs: notice » suivi, sans ligne vide,
    d'une ligne de corps, est découpé en mode auto comme deux sujets (aucune ligne vide →
    une ligne par message) : l'outil juge la 2e ligne comme un en-tête non conforme au lieu
    de dire « corps collé à l'en-tête ». `--mode unique` donne le bon verdict.
INVOCATION
    {outil} --message "feat(api): ajouter la pagination" --message "fix: corriger le tri" --depuis 1.4.2 --json
DOMAINE
    Messages de commit d'un projet qui suit Conventional Commits 1.0 et SemVer 2.0 : contrôle
    avant fusion, calcul de la prochaine version, génération de notes de version.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

try:
    from commitizen import bump as cz_bump
    from commitizen import git as cz_git
    from commitizen.config.base_config import BaseConfig as CzConfig
    from commitizen.cz.conventional_commits import conventional_commits as cz_cc
except ImportError:
    cz_bump = None

RACINE = Path(__file__).resolve().parent

TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_INVOCATION = "INVOCATION"
TITRES_CONTRAT = ("QUESTION", "MESURE", TITRE_HYPOTHESES, "LIMITES", "CONTRE-EXEMPLES",
                  TITRE_INVOCATION, "DOMAINE")
MOT_BREAKING = "BREAKING"
MOT_CHANGE = "CHANGE"
JETON_RUPTURE = f"{MOT_BREAKING} {MOT_CHANGE}"
JETON_RUPTURE_TIRET = "BREAKING-CHANGE"
ENCODAGE = "UTF-8"
NIVEAUX = ("aucune", "patch", "minor", "major")

MAX_OCTETS = 32 * 1024 * 1024
MAX_EXAMINES = 200
MOTIF_ENTETE = re.compile(
    r"^(?P<type>[A-Za-z][A-Za-z0-9_-]*)(?P<portee>\([^\r\n]*?\))?(?P<rupture>!)?"
    r"(?P<sep>:[ \t]*)(?P<description>.*)$")
MOTIF_PIED = re.compile(r"^(?P<cle>BREAKING CHANGE|[A-Za-z0-9][A-Za-z0-9-]*)(?P<sep>: | #)"
                        r"(?P<valeur>.*)$")
MOTIF_RUPTURE_MAL_ECRITE = re.compile(r"^(breaking[ -]change|Breaking[ -][Cc]hange)\s*:", re.I)
MOTIF_GIT_LOG = re.compile(r"^commit [0-9a-f]{7,64}\b")
MOTIF_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
MOTIFS_IGNORES = (
    re.compile(r"^Merge (pull request|branch|remote-tracking branch|tag) "),
    re.compile(r"^Merge .+ into .+"),
    re.compile(r"^Merged .+ (in|into) .+"),
    re.compile(r"^Revert \".*\""),
    re.compile(r"^(fixup|squash|amend)! "),
    re.compile(r"^Automatic merge"),
    re.compile(r"^Auto-merged .+ into .+"),
)


class ErreurEntree(Exception):
    """Entrée invalide (code 2) ou rien à examiner (code 3)."""

    def __init__(self, message: str, code: int = 2) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class Verdict:
    """Jugement d'un message."""

    rang: int
    entete: str
    ignore: str | None = None
    type: str | None = None
    portee: str | None = None
    rupture: bool = False
    sources_rupture: list[str] = field(default_factory=list)
    description: str | None = None
    corps: str = ""
    pieds: list[dict[str, str]] = field(default_factory=list)
    erreurs: list[str] = field(default_factory=list)
    avertissements: list[str] = field(default_factory=list)
    impact: str = "aucune"


# ---------------------------------------------------------------------------
# Lecture des messages
# ---------------------------------------------------------------------------

def decoder_echappements(texte: str) -> str:
    """\\n et \\t d'un message en ligne qui ne contient aucun vrai saut de ligne."""
    if "\n" in texte:
        return texte
    return re.sub(r"\\([nt\\])", lambda m: {"n": "\n", "t": "\t", "\\": "\\"}[m.group(1)], texte)


def lire_fichier(chemin: Path) -> str:
    """Lit le fichier de messages (borné, UTF-8 strict)."""
    if not chemin.exists():
        raise ErreurEntree(f"chemin introuvable : {chemin}")
    if not chemin.is_file():
        raise ErreurEntree(f"{chemin} n'est pas un fichier (attendu : un fichier de messages)")
    try:
        with chemin.open("rb") as flux:
            donnees = flux.read(MAX_OCTETS + 1)
    except OSError as exc:
        raise ErreurEntree(f"lecture impossible de {chemin} : {exc.strerror}") from exc
    if len(donnees) > MAX_OCTETS:
        raise ErreurEntree(f"{chemin} dépasse {MAX_OCTETS // (1024 * 1024)} Mo")
    try:
        return donnees.decode(ENCODAGE)
    except UnicodeDecodeError as exc:
        raise ErreurEntree(f"{chemin} n'est pas du texte {ENCODAGE} (octet {exc.start}) : "
                           "fichier binaire ?") from exc


def decouper_git_log(texte: str) -> list[str]:
    """Sortie `git log` par défaut : messages indentés de 4 espaces après chaque « commit »."""
    messages: list[list[str]] = []
    for ligne in texte.split("\n"):
        if MOTIF_GIT_LOG.match(ligne):
            messages.append([])
        elif messages and ligne.startswith("    "):
            messages[-1].append(ligne[4:])
        elif messages and ligne == "" and messages[-1]:
            messages[-1].append("")
    return ["\n".join(m) for m in messages]


def choisir_mode(texte: str, mode: str) -> str:
    """Mode effectif : NUL, git log, une ligne par message ou message unique."""
    if mode != "auto":
        return mode
    if "\x00" in texte:
        return "nul"
    premiere = next((l for l in texte.split("\n") if l.strip()), "")
    if MOTIF_GIT_LOG.match(premiere):
        return "git-log"
    lignes = [l for l in texte.strip("\n").split("\n")]
    if len(lignes) > 1 and all(l.strip() for l in lignes):
        return "ligne"
    return "unique"


def decouper(texte: str, mode: str, separateur: str | None) -> tuple[list[str], str]:
    """Découpe le texte en messages selon le mode (ou le séparateur donné)."""
    texte = texte.replace("\r\n", "\n")
    if separateur is not None:
        blocs = re.split(rf"^{re.escape(separateur)}[ \t]*$", texte, flags=re.MULTILINE)
        return [b.strip("\n") for b in blocs if b.strip()], "separateur"
    mode = choisir_mode(texte, mode)
    if mode == "nul":
        blocs = texte.split("\x00")
    elif mode == "git-log":
        blocs = decouper_git_log(texte)
    elif mode == "ligne":
        blocs = texte.split("\n")
    else:
        blocs = [texte]
    return [b.strip("\n") for b in blocs if b.strip()], mode


# ---------------------------------------------------------------------------
# Jugement d'un message
# ---------------------------------------------------------------------------

def motif_ignore(entete: str) -> str | None:
    """Commit de fusion, de retour automatique ou fixup! : écarté comme le fait commitlint."""
    for motif in MOTIFS_IGNORES:
        if motif.match(entete):
            return motif.pattern
    return None


def juger_entete(verdict: Verdict, entete: str, types: set[str] | None) -> None:
    """Type, portée, « ! », séparateur et description."""
    trouve = MOTIF_ENTETE.match(entete)
    if not trouve:
        verdict.erreurs.append("en-tête sans « type: » (ou « type(portée): ») en tête")
        return
    verdict.type = trouve.group("type").lower()
    portee = trouve.group("portee")
    if portee is not None:
        verdict.portee = portee[1:-1]
        if not verdict.portee.strip():
            verdict.erreurs.append("portée vide « () »")
        elif "(" in verdict.portee or ")" in verdict.portee:
            verdict.erreurs.append("parenthèse dans la portée")
    verdict.rupture = trouve.group("rupture") is not None
    if verdict.rupture:
        verdict.sources_rupture.append("!")
    juger_separateur(verdict, trouve.group("sep"), trouve.group("description"))
    if types is not None and verdict.type not in types:
        verdict.erreurs.append(f"type « {verdict.type} » hors de la liste autorisée")


def juger_separateur(verdict: Verdict, separateur: str, description: str) -> None:
    """« : » doit être suivi d'exactement une espace, puis d'une description non vide."""
    if separateur == ":":
        verdict.erreurs.append("pas d'espace après « : »" if description else "description vide")
        return
    if separateur != ": ":
        verdict.avertissements.append("blanc(s) en trop ou tabulation après « : »")
    verdict.description = description.strip()
    if not verdict.description:
        verdict.erreurs.append("description vide")


def est_pied(ligne: str) -> bool:
    """La ligne ouvre-t-elle un pied de page (jeton + « : » ou « #») ?"""
    return MOTIF_PIED.match(ligne) is not None or ligne.startswith(JETON_RUPTURE_TIRET + ": ")


def debut_des_pieds(lignes: list[str]) -> int | None:
    """Indice de la première ligne de pied : premier BREAKING CHANGE, sinon dernier paragraphe."""
    for i in range(2, len(lignes)):
        if lignes[i - 1] == "" and lignes[i].startswith((JETON_RUPTURE + ": ",
                                                        JETON_RUPTURE_TIRET + ": ")):
            return i
    dernier = max((i for i in range(2, len(lignes)) if lignes[i - 1] == "" and lignes[i]),
                  default=None)
    if dernier is not None and est_pied(lignes[dernier]):
        return dernier
    return None


def lire_pieds(verdict: Verdict, lignes: list[str]) -> None:
    """Pieds de page : jetons, valeurs multi-lignes, rupture."""
    for ligne in lignes:
        trouve = MOTIF_PIED.match(ligne)
        if ligne.startswith(JETON_RUPTURE_TIRET + ": "):
            verdict.pieds.append({"cle": JETON_RUPTURE_TIRET, "valeur": ligne[len(
                JETON_RUPTURE_TIRET) + 2:]})
        elif trouve:
            verdict.pieds.append({"cle": trouve.group("cle"), "valeur": trouve.group("valeur")})
        elif verdict.pieds:
            verdict.pieds[-1]["valeur"] += "\n" + ligne
    for pied in verdict.pieds:
        pied["valeur"] = pied["valeur"].rstrip("\n")
        if pied["cle"] in (JETON_RUPTURE, JETON_RUPTURE_TIRET):
            verdict.rupture = True
            verdict.sources_rupture.append(pied["cle"])
            if not pied["valeur"].strip():
                verdict.erreurs.append(f"pied {pied['cle']} sans description")


def juger_corps(verdict: Verdict, lignes: list[str]) -> None:
    """Ligne vide après l'en-tête, corps libre, pieds, pièges de casse."""
    if len(lignes) > 1 and lignes[1].strip():
        verdict.erreurs.append("le corps doit être séparé de l'en-tête par une ligne vide")
    debut = debut_des_pieds(lignes)
    fin_corps = debut if debut is not None else len(lignes)
    verdict.corps = "\n".join(lignes[1:fin_corps]).strip("\n")
    if debut is not None:
        lire_pieds(verdict, lignes[debut:])
    for ligne in lignes[1:]:
        if MOTIF_RUPTURE_MAL_ECRITE.match(ligne) and not ligne.startswith(
                (JETON_RUPTURE, JETON_RUPTURE_TIRET)):
            verdict.avertissements.append(
                f"« {ligne.split(':')[0]} » n'est pas reconnu : la rupture s'écrit "
                f"{JETON_RUPTURE} en majuscules")


def impact_de(verdict: Verdict, patch_aussi: set[str]) -> str:
    """Effet SemVer d'un message conforme."""
    if verdict.erreurs or verdict.ignore:
        return "aucune"
    if verdict.rupture:
        return "major"
    if verdict.type == "feat":
        return "minor"
    if verdict.type == "fix" or verdict.type in patch_aussi:
        return "patch"
    return "aucune"


def juger(rang: int, message: str, types: set[str] | None, strict: bool,
          patch_aussi: set[str]) -> Verdict:
    """Jugement complet d'un message."""
    lignes = [l.rstrip() for l in message.split("\n")]
    entete = lignes[0]
    verdict = Verdict(rang=rang, entete=entete)
    if not strict:
        verdict.ignore = motif_ignore(entete)
        if verdict.ignore:
            return verdict
    juger_entete(verdict, entete, types)
    juger_corps(verdict, lignes)
    verdict.impact = impact_de(verdict, patch_aussi)
    return verdict


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

def monter(version: str, niveau: str) -> str:
    """Version suivante (règle npm : une pré-version se « termine » si possible)."""
    trouve = MOTIF_VERSION.match(version)
    if trouve is None:
        raise ErreurEntree(f"version de départ non SemVer : {version}")
    majeur, mineur, correctif = (int(trouve.group(k)) for k in (1, 2, 3))
    pre = trouve.group(4)
    if niveau == "aucune":
        return f"{majeur}.{mineur}.{correctif}" + (f"-{pre}" if pre else "")
    if niveau == "major":
        return f"{majeur}.0.0" if pre and mineur == 0 and correctif == 0 else f"{majeur + 1}.0.0"
    if niveau == "minor":
        return f"{majeur}.{mineur}.0" if pre and correctif == 0 else f"{majeur}.{mineur + 1}.0"
    return f"{majeur}.{mineur}.{correctif}" if pre else f"{majeur}.{mineur}.{correctif + 1}"


def niveau_global(verdicts: list[Verdict], rester_en_zero: bool, depart: str | None) -> str:
    """Niveau le plus fort ; en 0.y.z avec --rester-en-zero, une rupture monte la mineure."""
    niveau = max((v.impact for v in verdicts), key=NIVEAUX.index, default="aucune")
    trouve = MOTIF_VERSION.match(depart or "")
    if niveau == "major" and rester_en_zero and trouve and trouve.group(1) == "0":
        return "minor"
    return niveau


# ---------------------------------------------------------------------------
# Contrôle croisé optionnel
# ---------------------------------------------------------------------------

def controler_commitizen(messages: list[str], verdicts: list[Verdict],
                         rester_en_zero: bool) -> dict[str, Any] | None:
    """Juge chaque message avec le motif de commitizen et recalcule sa montée."""
    if cz_bump is None:
        return None
    regle = cz_cc.ConventionalCommitsCz(CzConfig())
    motif = re.compile(regle.schema_pattern())
    desaccords = []
    for message, verdict in zip(messages, verdicts):
        if verdict.ignore:
            continue
        cz_conforme = motif.match(message) is not None
        if cz_conforme != (not verdict.erreurs):
            desaccords.append({"rang": verdict.rang, "entete": verdict.entete[:120],
                               "nous": not verdict.erreurs, "commitizen": cz_conforme})
    retenus = [cz_git.GitCommit(rev=str(v.rang), title=m.split("\n", 1)[0],
                                body=m.split("\n", 1)[1] if "\n" in m else "")
               for m, v in zip(messages, verdicts) if not v.ignore]
    carte = regle.bump_map_major_version_zero if rester_en_zero else regle.bump_map
    increment = cz_bump.find_increment(retenus, regle.bump_pattern, carte)
    return {"bibliotheque": "commitizen", "desaccords_conformite": desaccords[:MAX_EXAMINES],
            "montee_commitizen": (increment or "aucune").lower()}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def contrat() -> dict[str, str]:
    """Contrat de mesure extrait de la docstring."""
    texte = __doc__ or ""
    sections: dict[str, str] = {}
    for rang, titre in enumerate(TITRES_CONTRAT):
        suivants = "|".join(re.escape(t) for t in TITRES_CONTRAT[rang + 1:]) or r"\Z"
        trouve = re.search(rf"^{re.escape(titre)}\s*\n(.*?)(?=^(?:{suivants})\s*$|\Z)", texte,
                           re.MULTILINE | re.DOTALL)
        sections[titre] = " ".join(trouve.group(1).split()) if trouve else ""
    return sections


def decrire(verdict: Verdict) -> dict[str, Any]:
    """Verdict en JSON."""
    return {"rang": verdict.rang, "entete": verdict.entete, "conforme":
            None if verdict.ignore else not verdict.erreurs, "ignore": verdict.ignore,
            "type": verdict.type, "portee": verdict.portee, "rupture": verdict.rupture,
            "sources_rupture": verdict.sources_rupture, "description": verdict.description,
            "corps": bool(verdict.corps), "pieds": verdict.pieds, "erreurs": verdict.erreurs,
            "avertissements": verdict.avertissements, "impact": verdict.impact}


def charger_messages(args: argparse.Namespace) -> tuple[list[str], str, str]:
    """Messages à juger, mode de découpe, origine."""
    if args.message:
        return [decoder_echappements(m) for m in args.message if m.strip()], "en ligne", \
            "--message"
    if args.fichier is None:
        raise ErreurEntree("donner un fichier de messages ou au moins un --message")
    chemin = Path(args.fichier)
    if not chemin.is_absolute() and args.racine:
        chemin = Path(args.racine) / chemin
    messages, mode = decouper(lire_fichier(chemin), args.mode, args.separateur)
    return messages, mode, str(chemin)


def liste_option(valeur: str | None) -> set[str] | None:
    """« a,b,c » → {"a", "b", "c"} en minuscules."""
    if valeur is None:
        return None
    return {t.strip().lower() for t in valeur.split(",") if t.strip()}


def analyser(args: argparse.Namespace) -> dict[str, Any]:
    """Juge tous les messages et calcule la montée de version."""
    messages, mode, origine = charger_messages(args)
    if not messages:
        raise ErreurEntree("aucun message de commit dans l'entrée : dénominateur nul, "
                           "rien à examiner", code=3)
    patch_aussi = liste_option(args.patch_aussi) or set()
    verdicts = [juger(rang, m, liste_option(args.types), args.strict, patch_aussi)
                for rang, m in enumerate(messages, start=1)]
    niveau = niveau_global(verdicts, args.rester_en_zero, args.depuis)
    juges = [v for v in verdicts if not v.ignore]
    rapport: dict[str, Any] = {
        "outil": "lire_commits_conventionnels", "moteur": "stdlib", "contrat": contrat(),
        "origine": origine, "decoupe": mode, "denominateur": len(verdicts),
        "examines": [v.entete[:80] for v in verdicts[:MAX_EXAMINES]],
        "examines_tronques": len(verdicts) > MAX_EXAMINES,
        "conformes": sum(1 for v in juges if not v.erreurs),
        "non_conformes": sum(1 for v in juges if v.erreurs),
        "ignores": len(verdicts) - len(juges),
        "types": dict(sorted(compter_types(juges).items())),
        "montee": {"niveau": niveau, "regle": regle_montee(patch_aussi, args.rester_en_zero),
                   "depuis": args.depuis,
                   "version_suivante": monter(args.depuis, niveau) if args.depuis else None,
                   "determinants": [v.entete[:120] for v in juges if v.impact == niveau
                                    and niveau != "aucune"][:MAX_EXAMINES]},
        "messages": [decrire(v) for v in verdicts],
    }
    controle = controler_commitizen(messages, verdicts, args.rester_en_zero)
    if controle:
        rapport["moteur"] = "stdlib+commitizen"
        rapport["controle"] = controle
    return rapport


def compter_types(verdicts: list[Verdict]) -> dict[str, int]:
    """Nombre de messages conformes par type."""
    compte: dict[str, int] = {}
    for verdict in verdicts:
        if verdict.type and not verdict.erreurs:
            compte[verdict.type] = compte.get(verdict.type, 0) + 1
    return compte


def regle_montee(patch_aussi: set[str], rester_en_zero: bool) -> str:
    """Règle appliquée, en clair."""
    autres = f", {', '.join(sorted(patch_aussi))} → patch" if patch_aussi else ""
    zero = " ; en 0.y.z une rupture monte la mineure" if rester_en_zero else ""
    return f"rupture → major, feat → minor, fix → patch{autres}, autres → aucune{zero}"


# ---------------------------------------------------------------------------
# Affichage
# ---------------------------------------------------------------------------

def afficher_humain(rapport: dict[str, Any]) -> None:
    """Résumé lisible."""
    print(f"{rapport['denominateur']} message(s) ({rapport['decoupe']}) : "
          f"{rapport['conformes']} conforme(s), {rapport['non_conformes']} non conforme(s), "
          f"{rapport['ignores']} écarté(s)")
    for m in rapport["messages"]:
        if m["ignore"]:
            continue
        marque = "OK " if m["conforme"] else "NON"
        rupture = " [RUPTURE]" if m["rupture"] else ""
        print(f"  {marque} #{m['rang']} {m['entete'][:90]}{rupture}")
        for erreur in m["erreurs"]:
            print(f"        erreur : {erreur}")
        for avert in m["avertissements"]:
            print(f"        attention : {avert}")
    montee = rapport["montee"]
    suite = f" : {montee['depuis']} → {montee['version_suivante']}" if montee["depuis"] else ""
    print(f"\nMontée de version : {montee['niveau']}{suite}")
    controle = rapport.get("controle")
    if controle:
        print(f"commitizen : montée {controle['montee_commitizen']}, "
              f"{len(controle['desaccords_conformite'])} désaccord(s) de conformité")


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------

def construire_parseur() -> argparse.ArgumentParser:
    """Parseur de la ligne de commande."""
    parseur = argparse.ArgumentParser(
        description="Contrôle des messages de commit selon Conventional Commits 1.0 et calcul "
                    "de la version SemVer suivante.",
        epilog="Exemple : git log --format=%%B%%x00 v1.4.2..HEAD > msgs.txt ; "
               "python outils/lire_commits_conventionnels.py msgs.txt --depuis 1.4.2 --json")
    parseur.add_argument("fichier", nargs="?", help="fichier de messages (NUL, git log, une "
                                                    "ligne par message, ou séparateur)")
    parseur.add_argument("--message", "-m", action="append", default=[],
                         help="message en ligne (répétable) ; \\n y marque un saut de ligne")
    parseur.add_argument("--mode", choices=("auto", "nul", "git-log", "ligne", "unique"),
                         default="auto", help="découpe du fichier (défaut : auto)")
    parseur.add_argument("--separateur", help="ligne qui sépare les messages (ex. ---)")
    parseur.add_argument("--depuis", help="version de départ X.Y.Z[-pré] pour calculer la suivante")
    parseur.add_argument("--types", help="types autorisés, séparés par des virgules")
    parseur.add_argument("--patch-aussi", help="types qui montent aussi le correctif (ex. perf)")
    parseur.add_argument("--rester-en-zero", action="store_true",
                         help="en 0.y.z, une rupture monte la mineure au lieu de passer en 1.0.0")
    parseur.add_argument("--strict", action="store_true",
                         help="juge aussi les fusions, retours automatiques et fixup!")
    parseur.add_argument("--racine", help="dossier de base des chemins relatifs (défaut : courant)")
    parseur.add_argument("--json", action="store_true", help="un seul objet JSON sur stdout")
    return parseur


def main() -> int:
    """Point d'entrée."""
    args = construire_parseur().parse_args()
    if cz_bump is None:
        print("commitizen absent : jugement stdlib seul, sans contre-jugement par commitizen",
              file=sys.stderr)
    try:
        if args.depuis and not MOTIF_VERSION.match(args.depuis):
            raise ErreurEntree(f"--depuis n'est pas une version SemVer : {args.depuis}")
        rapport = analyser(args)
    except ErreurEntree as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        if args.json:
            print(json.dumps({"outil": "lire_commits_conventionnels", "moteur": "stdlib",
                              "denominateur": 0, "examines": [], "erreur": str(exc)},
                             ensure_ascii=False))
        return exc.code
    if args.json:
        print(json.dumps(rapport, ensure_ascii=False, indent=2))
    else:
        afficher_humain(rapport)
    return 1 if rapport["non_conformes"] else 0


__all__ = ["juger", "monter", "decouper", "analyser", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
