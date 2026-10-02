"""Détecter les secrets en clair dans un fichier ou un dépôt, sans jamais les réafficher.

Chercher des secrets avec grep les recopie en clair dans le journal de l'agent : sur un
fichier témoin de 9 lignes (6 secrets factices, 3 leurres), `grep -inE
'key|secret|token|password'` a sorti 6 lignes dont 3 leurres, a imprimé 2 secrets en
entier et en a manqué 3 (jeton Slack, JWT, URL de connexion) ; cet outil a trouvé les
6, masqués, sans retenir de leurre.

QUESTION
    Ce fichier ou ce dépôt contient-il des secrets en clair (clés d'API, jetons,
    clés privées, mots de passe dans une URL de connexion) ?
MESURE
    Lecture ligne à ligne (en flux, lignes bornées) des fichiers texte donnés ou
    trouvés sous les dossiers donnés. Deux familles de règles : (1) motifs de
    fournisseurs à préfixe reconnaissable (AWS, GitHub, Slack, Stripe, Google,
    OpenAI, Anthropic, GitLab, npm, PyPI, SendGrid, Hugging Face, blocs de clé
    privée PEM, JWT dont l'en-tête décodé porte « alg », URL de connexion avec mot
    de passe) ; (2) entropie de Shannon (bits par caractère) des valeurs littérales
    affectées à un nom suspect (password, secret, token, api_key, jeton...).
    Le commentaire « pragma: allowlist secret » (sur la ligne) ou « pragma:
    allowlist nextline secret » (ligne précédente) exempte la ligne : le constat
    est alors compté à part. Si detect-secrets est installé, il est passé sur les
    mêmes fichiers ; ses constats supplémentaires sont ajoutés (source indiquée) et
    la comparaison ligne à ligne est rendue. Aucun secret n'est jamais affiché en
    entier : seuls ses premiers caractères (4 au plus, moins s'il est court) et sa
    longueur sortent.
HYPOTHÈSES
    Les secrets sont écrits en clair dans des fichiers texte (utf-8 ou proche) ;
    les noms de variables suivent les conventions courantes (anglais ou français,
    snake_case ou camelCase) ; les formats de jetons des fournisseurs n'ont pas
    changé depuis l'écriture des motifs.
LIMITES
    Ne lit ni l'historique git, ni les fichiers binaires (octet nul dans les 8 premiers
    Kio), ni les archives, ni les fichiers au-delà de --taille-max ; ne suit pas les
    liens symboliques. Un secret encodé (base64 d'un base64, chiffré, découpé sur
    plusieurs lignes ou concaténé) échappe. Un mot de passe humain court ou à faible
    entropie affecté à un nom suspect peut passer sous le seuil. Les dossiers .git,
    node_modules, __pycache__, venv et .venv sont sautés sauf --tout (et listés).
    Aucune vérification en ligne de la validité des clés. Avec detect-secrets, l'import
    de sa dépendance urllib3 ouvre une socket locale (sonde IPv6 sur ::1, sans connexion
    sortante) dès le premier fichier lu ; --stdlib l'évite.
CONTRE-EXEMPLES
    Faux positif constaté : token_alphabet = "<les 32 caractères autorisés pour
    générer des jetons>" est signalé (entropie 5,0) alors qu'il n'est pas secret. Faux négatifs constatés : le JWT d'exemple de jwt.io coupé
    sur trois littéraux dans outils/verifier_signature_jwt.py (lignes 137 à 139)
    n'est pas vu (detect-secrets en voit le troisième segment) ; un mot de passe
    humain de 8 caractères comme hunter22 (2,75 bits/car.) passe sous le seuil 3,0.
INVOCATION
    {outil} {dossier} --json
DOMAINE
    Dépôts de code, fichiers de configuration (.env, .yaml, .json, .ini), scripts et
    journaux texte, avant un commit, une publication ou un partage avec un tiers.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import math
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from importlib import metadata, util
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent

__all__ = ["analyser_chemins", "analyser_ligne", "entropie_shannon", "masquer", "main"]

TITRE_HYPOTHESES = "HYPOTHÈSES"
TITRE_INVOCATION = "INVOCATION"
TITRES_CONTRAT = ("QUESTION", "MESURE", TITRE_HYPOTHESES, "LIMITES",
                  "CONTRE-EXEMPLES", TITRE_INVOCATION, "DOMAINE")

CODE_RIEN = 0
CODE_TROUVE = 1
CODE_USAGE = 2
CODE_VIDE = 3

TAILLE_SONDE = 8192
LIGNE_MAX = 1 << 20
EXAMINES_MAX = 200
DOSSIERS_SAUTES = frozenset({".git", ".hg", ".svn", "node_modules", "__pycache__",
                             ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache",
                             ".ruff_cache"})
PRAGMA_LIGNE = "pragma: allowlist secret"
PRAGMA_SUIVANTE = "pragma: allowlist nextline secret"
TYPES_TIERS_IGNORES = frozenset({"Public IP (ipv4)"})

MOTS_SUSPECTS = frozenset({"password", "passwd", "passphrase", "pwd", "secret", "token",
                           "apikey", "credential", "credentials", "jeton", "passe",
                           "secretkey", "accesskey", "privatekey", "authkey"})
PAIRES_SUSPECTES = frozenset({("api", "key"), ("access", "key"), ("private", "key"),
                              ("secret", "key"), ("auth", "key"), ("client", "secret"),
                              ("cle", "api"), ("mot", "de"), ("signing", "key")})
MOTS_NEUTRES = frozenset({"hash", "url", "uri", "endpoint", "type", "name", "nom", "file",
                          "path", "chemin", "env", "var", "field", "length", "len", "min",
                          "max", "expiry", "expires", "timeout", "header", "prefix", "regex",
                          "pattern", "format", "label", "count", "policy", "provider",
                          "class", "mode", "id", "ref", "kind", "size", "taille", "fichier",
                          "algorithm", "algo", "strength", "rotation", "scope", "scopes",
                          "version", "salt", "rounds", "duree", "longueur", "sha", "sha1",
                          "sha256", "md5", "digest", "checksum", "fingerprint", "empreinte"})
VALEURS_BANALES = frozenset({"password", "passwd", "secret", "pass", "none", "null", "true",
                             "false", "undefined", "todo", "fixme", "required", "optional",
                             "string", "str", "bytes", "motdepasse"})
FRAGMENTS_FACTICES = ("changeme", "change_me", "change-me", "example", "exemple", "dummy",
                      "placeholder", "redacted", "your_", "your-", "votre_", "<", "${",
                      "{{", "%s", "%(", "$(", "xxx", "***", "…", "...")

MOTIF_AFFECTATION = re.compile(
    r"(?<![A-Za-z0-9_])(?P<nom>[A-Za-z_][A-Za-z0-9_.\-]{0,80})[\"']?"
    r"(?:\s*:\s*[A-Za-z_][\w.\[\]|, ]{0,40}?\s*(?==))?"
    r"\s*(?::=|=>|=|:)\s*"
    r"(?:(?P<q>[\"'`])(?P<val_q>[^\"'`\r\n]{1,512})(?P=q)"
    r"|(?P<val_n>[^\s\"'`,;#()\[\]{}<>]{1,512})(?![\w(\[.]))"
)
MOTIF_MOTS_NOM = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")
MOTIF_IDENTIFIANT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


@dataclass(frozen=True)
class Regle:
    """Une règle de fournisseur : motif, groupe porteur du secret, contrôle facultatif."""

    nom: str
    description: str
    gravite: str
    motif: re.Pattern[str]
    verifier: Callable[[str], bool] | None = None


@dataclass(frozen=True)
class Constat:
    """Un secret présumé, jamais stocké en clair au-delà de l'analyse de sa ligne."""

    fichier: str
    ligne: int
    colonne: int
    regle: str
    description: str
    gravite: str
    apercu: str
    longueur: int
    source: str = "stdlib"
    entropie: float | None = None
    indice: str = ""


def entropie_shannon(texte: str) -> float:
    """Entropie de Shannon en bits par caractère (0 pour une chaîne vide)."""
    if not texte:
        return 0.0
    total = len(texte)
    return -sum((n / total) * math.log2(n / total) for n in Counter(texte).values())


def masquer(valeur: str) -> str:
    """Rend au plus 4 premiers caractères, moins si la valeur est courte, puis une ellipse."""
    visibles = 4 if len(valeur) >= 16 else len(valeur) // 4
    return valeur[:visibles] + "…"


def _est_factice(valeur: str) -> bool:
    """Vrai pour un gabarit, une référence de variable ou un mot banal."""
    bas = valeur.lower()
    if bas in VALEURS_BANALES or bas.startswith(("$", "%", "@")):
        return True
    if len(set(bas)) <= 2:
        return True
    return any(fragment in bas for fragment in FRAGMENTS_FACTICES)


def _jwt_plausible(jeton: str) -> bool:
    """L'en-tête d'un JWT se décode en JSON portant la clé « alg »."""
    entete = jeton.split(".", 1)[0]
    try:
        brut = base64.urlsafe_b64decode(entete + "=" * (-len(entete) % 4))
        return "alg" in json.loads(brut.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, ValueError, TypeError):
        return False


def _cle_variee(jeton: str) -> bool:
    """Une vraie clé mêle chiffres et lettres ; écarte « sk-very-long-css-class »."""
    return any(c.isdigit() for c in jeton) and any(c.isalpha() for c in jeton)


def _mot_de_passe_reel(mot: str) -> bool:
    """Le mot de passe d'une URL de connexion n'est ni un gabarit ni vide."""
    return bool(mot) and not _est_factice(mot)


REGLES = (
    Regle("aws_cle_acces", "Identifiant de clé d'accès AWS", "élevée",
          re.compile(r"(?<![A-Z0-9])((?:AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16})(?![A-Z0-9])")),
    Regle("github_jeton", "Jeton GitHub (ghp_, gho_, ghu_, ghs_, ghr_)", "élevée",
          re.compile(r"(?<![A-Za-z0-9_])(gh[pousr]_[A-Za-z0-9]{36,251})(?![A-Za-z0-9_])")),
    Regle("github_pat", "Jeton GitHub à grain fin (github_pat_)", "élevée",
          re.compile(r"(?<![A-Za-z0-9_])(github_pat_[A-Za-z0-9_]{50,244})")),
    Regle("slack_jeton", "Jeton Slack (xox*)", "élevée",
          re.compile(r"(?<![A-Za-z0-9-])(xox[abposre]-[A-Za-z0-9-]{10,250})"), _cle_variee),
    Regle("slack_webhook", "URL de webhook entrant Slack", "moyenne",
          re.compile(r"(https://hooks\.slack\.com/services/T[A-Z0-9]{6,}/B[A-Z0-9]{6,}/[A-Za-z0-9]{16,})")),
    Regle("stripe_cle_live", "Clé secrète Stripe de production", "critique",
          re.compile(r"(?<![A-Za-z0-9_])((?:sk|rk)_live_[A-Za-z0-9]{20,247})")),
    Regle("stripe_cle_test", "Clé secrète Stripe de test", "faible",
          re.compile(r"(?<![A-Za-z0-9_])((?:sk|rk)_test_[A-Za-z0-9]{20,247})")),
    Regle("google_cle_api", "Clé d'API Google (AIza)", "élevée",
          re.compile(r"(?<![A-Za-z0-9_-])(AIza[0-9A-Za-z_-]{35})(?![A-Za-z0-9_-])")),
    Regle("cle_privee_pem", "Début d'un bloc de clé privée PEM", "critique",
          re.compile(r"(-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----)")),
    Regle("anthropic_cle", "Clé d'API Anthropic (sk-ant-)", "élevée",
          re.compile(r"(?<![A-Za-z0-9_-])(sk-ant-[a-z0-9]{2,12}-[A-Za-z0-9_-]{32,})"), _cle_variee),
    Regle("openai_cle", "Clé d'API OpenAI (sk-, sk-proj-)", "élevée",
          re.compile(r"(?<![A-Za-z0-9_-])(sk-(?!ant-)(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{32,})"),
          _cle_variee),
    Regle("gitlab_jeton", "Jeton d'accès personnel GitLab (glpat-)", "élevée",
          re.compile(r"(?<![A-Za-z0-9_-])(glpat-[A-Za-z0-9_-]{20,})")),
    Regle("npm_jeton", "Jeton npm (npm_)", "élevée",
          re.compile(r"(?<![A-Za-z0-9_])(npm_[A-Za-z0-9]{36})(?![A-Za-z0-9])")),
    Regle("pypi_jeton", "Jeton d'API PyPI", "élevée",
          re.compile(r"(pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,})")),
    Regle("sendgrid_cle", "Clé d'API SendGrid", "élevée",
          re.compile(r"(?<![A-Za-z0-9_])(SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43})")),
    Regle("huggingface_jeton", "Jeton Hugging Face (hf_)", "élevée",
          re.compile(r"(?<![A-Za-z0-9_])(hf_[A-Za-z0-9]{34,})(?![A-Za-z0-9])"), _cle_variee),
    Regle("jwt", "Jeton JWT signé", "moyenne",
          re.compile(r"(?<![A-Za-z0-9_-])(eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})"),
          _jwt_plausible),
    Regle("chaine_connexion", "Mot de passe dans une URL de connexion", "élevée",
          re.compile(r"(?i)\b[a-z][a-z0-9+.-]{1,30}://[^\s:/@'\"`<>]{1,128}:([^\s@/'\"`<>]{1,256})@[A-Za-z0-9._\[\]-]+"),
          _mot_de_passe_reel),
)


def _mots_du_nom(nom: str) -> list[str]:
    """Découpe snake_case, kebab-case, notation pointée et camelCase en mots minuscules."""
    return [m.lower() for m in MOTIF_MOTS_NOM.findall(nom)]


def nom_suspect(nom: str) -> bool:
    """Vrai si le nom de variable évoque un secret et n'est pas un simple descripteur."""
    mots = _mots_du_nom(nom)
    positions = [i for i, m in enumerate(mots) if m in MOTS_SUSPECTS]
    positions += [i + 1 for i, paire in enumerate(zip(mots, mots[1:])) if paire in PAIRES_SUSPECTES]
    if not positions:
        return False
    return not any(m in MOTS_NEUTRES for m in mots[min(positions) + 1:])


def _valeur_candidate(valeur: str, citee: bool, longueur_min: int) -> bool:
    """Filtre les valeurs qui ne peuvent pas être un secret littéral."""
    if len(valeur) < longueur_min or _est_factice(valeur):
        return False
    if any(c.isspace() for c in valeur) or "://" in valeur:
        return False
    if valeur.startswith(("/", "./", "~/", "../")) or valeur.count("/") >= 3:
        return False
    if not citee and MOTIF_IDENTIFIANT.fullmatch(valeur) and not any(c.isdigit() for c in valeur):
        return False
    return True


def _constats_fournisseurs(ligne: str, fichier: str, numero: int) -> list[Constat]:
    """Applique les motifs de fournisseurs à une ligne."""
    constats: list[Constat] = []
    for regle in REGLES:
        for m in regle.motif.finditer(ligne):
            secret = m.group(1)
            if regle.verifier is not None and not regle.verifier(secret):
                continue
            indice = "exemple de documentation probable" if "EXAMPLE" in secret.upper() else ""
            constats.append(Constat(fichier, numero, m.start(1) + 1, regle.nom, regle.description,
                                    regle.gravite, masquer(secret), len(secret), indice=indice))
    return constats


def _constats_entropie(ligne: str, fichier: str, numero: int, seuils: tuple[float, int],
                       deja: set[int]) -> list[Constat]:
    """Valeurs littérales à forte entropie affectées à un nom suspect."""
    entropie_min, longueur_min = seuils
    constats: list[Constat] = []
    for m in MOTIF_AFFECTATION.finditer(ligne):
        citee = m.group("val_q") is not None
        valeur = m.group("val_q") if citee else m.group("val_n")
        debut = m.start("val_q") if citee else m.start("val_n")
        if debut in deja or not nom_suspect(m.group("nom")):
            continue
        if not _valeur_candidate(valeur, citee, longueur_min):
            continue
        h = entropie_shannon(valeur)
        if h < entropie_min:
            continue
        constats.append(Constat(fichier, numero, debut + 1, "entropie_nom_suspect",
                                f"Valeur à forte entropie affectée à « {m.group('nom')} »",
                                "moyenne", masquer(valeur), len(valeur), entropie=round(h, 2)))
    return constats


def analyser_ligne(ligne: str, fichier: str, numero: int,
                   seuils: tuple[float, int] = (3.0, 8)) -> list[Constat]:
    """Tous les constats d'une ligne : fournisseurs d'abord, entropie sur le reste."""
    constats = _constats_fournisseurs(ligne, fichier, numero)
    deja = {c.colonne - 1 for c in constats}
    occupe = [(c.colonne - 1, c.colonne - 1 + c.longueur) for c in constats]
    for c in _constats_entropie(ligne, fichier, numero, seuils, deja):
        debut = c.colonne - 1
        if not any(a <= debut < b for a, b in occupe):
            constats.append(c)
    return constats


def _sonder(chemin: Path) -> str:
    """Rend '' si le fichier est un texte lisible, sinon la raison de l'écarter."""
    try:
        with chemin.open("rb") as flux:
            debut = flux.read(TAILLE_SONDE)
    except OSError as exc:
        return f"illisible ({exc.strerror or exc})"
    if b"\x00" in debut:
        return "binaire (octet nul)"
    controles = sum(1 for o in debut if o < 32 and o not in (9, 10, 12, 13) or o == 127)
    if debut and controles / len(debut) > 0.3:
        return "binaire (caractères de contrôle)"
    return ""


def _lignes(chemin: Path) -> Iterator[tuple[int, str]]:
    """Lignes numérotées, lues en flux ; une ligne géante est découpée en segments bornés."""
    numero = 1
    with chemin.open("r", encoding="utf-8", errors="replace", newline="") as flux:
        while segment := flux.readline(LIGNE_MAX):
            yield numero, segment
            if segment.endswith(("\n", "\r")):
                numero += 1


def analyser_fichier(chemin: Path, nom: str, seuils: tuple[float, int]) -> tuple[list[Constat], list[Constat]]:
    """Rend (constats, exemptés) pour un fichier texte."""
    constats: list[Constat] = []
    exemptes: list[Constat] = []
    exempter_suivante = False
    for numero, ligne in _lignes(chemin):
        bas = ligne.lower()
        exempte = exempter_suivante or PRAGMA_LIGNE in bas
        exempter_suivante = PRAGMA_SUIVANTE in bas
        trouves = analyser_ligne(ligne, nom, numero, seuils)
        (exemptes if exempte else constats).extend(trouves)
    return constats, exemptes


def _version_tiers() -> str:
    """Version installée de detect-secrets, ou chaîne vide."""
    try:
        return metadata.version("detect-secrets")
    except metadata.PackageNotFoundError:
        return ""


def charger_tiers() -> tuple[object, object] | None:
    """Importe detect-secrets à la demande (son import ouvre une sonde IPv6 locale d'urllib3)."""
    try:
        from detect_secrets import SecretsCollection
        from detect_secrets.settings import default_settings
    except ImportError:
        return None
    return SecretsCollection, default_settings


def scanner_tiers(outils: tuple[object, object], chemin: Path, nom: str) -> tuple[list[Constat], str]:
    """Passe detect-secrets sur un fichier ; rend (constats, erreur éventuelle)."""
    collection_cls, reglages = outils
    collection = collection_cls()
    try:
        with reglages():
            collection.scan_file(str(chemin))
    except Exception as exc:  # bibliothèque tierce : toute panne est rapportée, pas masquée
        return [], f"{type(exc).__name__}: {exc}"
    constats = []
    for _fichier, secret in collection:
        if secret.type in TYPES_TIERS_IGNORES:
            continue
        valeur = secret.secret_value or ""
        constats.append(Constat(nom, secret.line_number, 0, f"detect-secrets:{secret.type}",
                                secret.type, "moyenne", masquer(valeur), len(valeur),
                                source="detect-secrets"))
    return constats, ""


def _fichiers_sous(dossier: Path, options: argparse.Namespace,
                   ignores: list[dict[str, str]]) -> Iterator[Path]:
    """Parcourt un dossier sans suivre les liens ; note les dossiers et liens sautés."""
    tout = options.tout
    for courant, sous, fichiers in os.walk(dossier):
        base = Path(courant)
        for nom in sorted(sous):
            if (nom in DOSSIERS_SAUTES and not tout) or (base / nom).is_symlink():
                ignores.append({"chemin": _nom_affiche(base / nom, options.base),
                                "raison": "dossier sauté (--tout pour l'inclure)"})
        sous[:] = sorted(n for n in sous if (tout or n not in DOSSIERS_SAUTES)
                         and not (base / n).is_symlink())
        for nom in sorted(fichiers):
            yield base / nom


def _cibles(chemins: list[Path], options: argparse.Namespace,
            ignores: list[dict[str, str]]) -> Iterator[Path]:
    """Développe les arguments en fichiers à examiner."""
    for chemin in chemins:
        if chemin.is_dir():
            yield from _fichiers_sous(chemin, options, ignores)
        else:
            yield chemin


def _nom_affiche(chemin: Path, base: Path) -> str:
    """Chemin relatif à la base si possible, sinon tel quel."""
    try:
        return str(chemin.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(chemin)


def _ecarter(chemin: Path, taille_max: int) -> str:
    """Raison d'écarter un fichier avant lecture, ou chaîne vide."""
    if chemin.is_symlink():
        return "lien symbolique non suivi"
    try:
        if chemin.stat().st_size > taille_max:
            return f"plus de {taille_max} octets"
    except OSError as exc:
        return f"illisible ({exc.strerror or exc})"
    return _sonder(chemin)


def _comparer(propres: list[Constat], tiers: list[Constat]) -> dict[str, object]:
    """Compare les lignes signalées par chaque moteur."""
    a = {(c.fichier, c.ligne) for c in propres}
    b = {(c.fichier, c.ligne) for c in tiers}
    return {
        "lignes_communes": len(a & b),
        "seulement_stdlib": [{"fichier": f, "ligne": n} for f, n in sorted(a - b)],
        "seulement_detect_secrets": [{"fichier": f, "ligne": n} for f, n in sorted(b - a)],
    }


@dataclass
class Collecte:
    """Ce que l'analyse accumule fichier après fichier."""

    tiers_actif: bool
    outils_tiers: tuple[object, object] | None = None
    examines: list[str] = field(default_factory=list)
    ignores: list[dict[str, str]] = field(default_factory=list)
    constats: list[Constat] = field(default_factory=list)
    exemptes: list[Constat] = field(default_factory=list)
    tiers: list[Constat] = field(default_factory=list)
    pannes: list[str] = field(default_factory=list)


def _passer_tiers(chemin: Path, nom: str, c: Collecte) -> None:
    """Passe detect-secrets sur un fichier déjà lu par les règles stdlib."""
    c.outils_tiers = c.outils_tiers or charger_tiers()
    if c.outils_tiers is None:
        c.tiers_actif = False
        return
    vus, panne = scanner_tiers(c.outils_tiers, chemin, nom)
    c.tiers.extend(vus)
    if panne:
        c.pannes.append(f"{nom}: {panne}")


def _examiner_fichier(chemin: Path, options: argparse.Namespace, c: Collecte) -> None:
    """Écarte, ou analyse un fichier et range ses constats."""
    nom = _nom_affiche(chemin, options.base)
    raison = _ecarter(chemin, options.taille_max)
    if raison:
        c.ignores.append({"chemin": nom, "raison": raison})
        return
    try:
        trouves, exemptes = analyser_fichier(chemin, nom, (options.entropie_min, options.longueur_min))
    except OSError as exc:
        c.ignores.append({"chemin": nom, "raison": f"illisible ({exc.strerror or exc})"})
        return
    c.examines.append(nom)
    c.constats.extend(trouves)
    c.exemptes.extend(exemptes)
    if c.tiers_actif:
        _passer_tiers(chemin, nom, c)


def analyser_chemins(chemins: list[Path], options: argparse.Namespace) -> dict[str, object]:
    """Analyse complète ; rend le rapport (sans le contrat)."""
    c = Collecte(tiers_actif=options.tiers)
    for chemin in _cibles(chemins, options, c.ignores):
        _examiner_fichier(chemin, options, c)
    return _rapport(c)


def _rapport(c: Collecte) -> dict[str, object]:
    """Assemble le rapport ; les constats tiers non vus par stdlib y sont ajoutés."""
    connues = {(x.fichier, x.ligne) for x in c.constats + c.exemptes}
    ajouts = [x for x in c.tiers if (x.fichier, x.ligne) not in connues]
    tous = sorted(c.constats + ajouts, key=lambda x: (x.fichier, x.ligne, x.colonne))
    rapport: dict[str, object] = {
        "denominateur": len(c.examines),
        "examines": c.examines[:EXAMINES_MAX],
        "examines_tronques": len(c.examines) > EXAMINES_MAX,
        "moteur": "detect-secrets" if c.tiers_actif else "stdlib",
        "verdict": "SECRETS TROUVÉS" if tous else "AUCUN SECRET",
        "nombre_constats": len(tous),
        "constats": [asdict(x) for x in tous],
        "exemptes": [asdict(x) for x in c.exemptes],
        "ignores": c.ignores,
    }
    if c.tiers_actif:
        rapport["comparaison"] = {"detect_secrets": _version_tiers(), "pannes": c.pannes,
                                  **_comparer(c.constats, c.tiers)}
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
    """Résumé lisible ; les secrets restent masqués."""
    print(f"{rapport['denominateur']} fichier(s) examiné(s) — moteur {rapport['moteur']} — "
          f"{rapport['verdict']} ({rapport['nombre_constats']} constat(s), "
          f"{len(rapport['exemptes'])} exempté(s) par pragma)")
    for c in rapport["constats"]:
        extra = f" entropie {c['entropie']}" if c["entropie"] is not None else ""
        indice = f" — {c['indice']}" if c["indice"] else ""
        print(f"  {c['fichier']}:{c['ligne']}:{c['colonne']}  [{c['gravite']}] {c['regle']}  "
              f"{c['apercu']} ({c['longueur']} car.){extra}{indice}  [{c['source']}]")
    for i in rapport["ignores"][:20]:
        print(f"  ignoré : {i['chemin']} — {i['raison']}")


def _parseur() -> argparse.ArgumentParser:
    """Interface en ligne de commande."""
    p = argparse.ArgumentParser(
        description="Cherche des secrets en clair (clés d'API, jetons, clés privées, mots de "
                    "passe) dans des fichiers ou des dossiers, sans jamais les réafficher.",
        epilog=f"Exemple : python {RACINE.name}/detecter_secrets.py . --json   "
               "(code 0 : rien ; 1 : secret trouvé ; 2 : entrée invalide ; 3 : rien à examiner)")
    p.add_argument("chemins", nargs="+", type=Path, help="fichiers ou dossiers à examiner")
    p.add_argument("--json", action="store_true", help="un objet JSON sur stdout")
    p.add_argument("--racine", type=Path, default=None,
                   help="base des chemins relatifs et des noms affichés (défaut : dossier courant)")
    p.add_argument("--entropie-min", type=float, default=3.0,
                   help="seuil d'entropie en bits/caractère pour les noms suspects (défaut 3.0)")
    p.add_argument("--longueur-min", type=int, default=8,
                   help="longueur minimale d'une valeur affectée à un nom suspect (défaut 8)")
    p.add_argument("--taille-max", type=float, default=10.0,
                   help="taille maximale d'un fichier lu, en Mo (défaut 10)")
    p.add_argument("--tout", action="store_true",
                   help="ne pas sauter .git, node_modules, venv, __pycache__...")
    p.add_argument("--stdlib", action="store_true",
                   help="n'utiliser que les règles stdlib même si detect-secrets est installé")
    return p


def _valider_entrees(options: argparse.Namespace) -> str:
    """Rend un message d'erreur d'usage, ou chaîne vide."""
    if not options.base.is_dir():
        return f"--racine n'est pas un dossier : {options.base}"
    if options.taille_max <= 0 or options.longueur_min < 1:
        return "--taille-max et --longueur-min doivent être positifs"
    for chemin in options.resolus:
        if not chemin.exists():
            return f"chemin introuvable : {chemin}"
    return ""


def main(argv: list[str] | None = None) -> int:
    """Point d'entrée : codes 0 (rien), 1 (secret), 2 (usage), 3 (rien à examiner)."""
    options = _parseur().parse_args(argv)
    options.base = options.racine if options.racine is not None else Path.cwd()
    options.resolus = [c if c.is_absolute() else options.base / c for c in options.chemins]
    erreur = _valider_entrees(options)
    if erreur:
        print(f"detecter_secrets : {erreur}", file=sys.stderr)
        return CODE_USAGE
    options.taille_max = int(options.taille_max * 1_000_000)
    options.tiers = not options.stdlib and util.find_spec("detect_secrets") is not None
    if not options.tiers and not options.stdlib:
        print("detecter_secrets : detect-secrets absent — mode dégradé stdlib "
              "(motifs fournisseurs + entropie), sans comparaison.", file=sys.stderr)
    rapport = analyser_chemins(options.resolus, options)
    rapport["contrat"] = extraire_contrat(__doc__ or "")
    if rapport["denominateur"] == 0:
        print("detecter_secrets : dénominateur nul — rien à examiner (aucun fichier texte "
              f"lisible ; {len(rapport['ignores'])} écarté(s)).", file=sys.stderr)
    if options.json:
        afficher_json(rapport)
    else:
        afficher_humain(rapport)
    if rapport["denominateur"] == 0:
        return CODE_VIDE
    return CODE_TROUVE if rapport["nombre_constats"] else CODE_RIEN


if __name__ == "__main__":
    raise SystemExit(main())
