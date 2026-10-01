"""Éprouve un outil en isolement total selon le socle.

Ce module exécute les contrôles décrits dans le socle, en s’assurant que le
détecteur de fuites (le « mouchard ») est bien armé.  Il génère un rapport JSON
contenant, entre autres, le résultat du contrôle positif du mouchard.

Codes de sortie — seul 0 veut dire « tous LIVRABLE » :
    0  tous les outils LIVRABLE
    1  au moins un REVERSE KO ou une FUITE
    2  au moins un FORWARD KO, BLOQUE, INAPPLICABLE ou verdict inconnu ; aucun outil trouvé
    3  dénominateur total nul
    4  mouchard AVEUGLE, ou au moins un outil NON EPROUVE (y compris par défaut du juge)
    5  contrôle positif NON MESURABLE, ou interpréteur introuvable
"""

from __future__ import annotations

import argparse
import builtins
import hashlib
import hmac
import json
import os
import platform
import re
import shlex
import shutil
import socket
import subprocess
import sys
import sysconfig
import tempfile
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple, Set

# Encodage en tête
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

RACINE = Path(__file__).resolve().parent.parent

__all__ = ["eprouver_isolement", "eprouver_tous"]

SENTINELLE_CONTENU = b"sentinel-constant-32-bytes!!"

def _est_conteneur() -> bool:
    """Détecte si l'exécution se fait dans un conteneur."""
    try:
        if Path("/.dockerenv").is_file():
            return True
    except OSError:
        pass

    try:
        cgroup = Path("/proc/1/cgroup")
        if cgroup.is_file() and ("docker" in cgroup.read_text() or "containerd" in cgroup.read_text()):
            return True
    except OSError:
        pass

    try:
        if os.environ.get("container") is not None:
            return True
    except Exception:
        pass

    return False

def _plateforme() -> Dict[str, Any]:
    """Retourne les informations sur la plateforme d'exécution."""
    systeme = platform.system()
    version = platform.release()
    machine = platform.machine()
    python_version = platform.python_version()
    conteneur = _est_conteneur()

    libelle = systeme + (' ' + version if systeme == 'Windows' else '')
    if conteneur:
        libelle += ' (conteneur)'

    return {
        "systeme": systeme,
        "version": version,
        "machine": machine,
        "python": python_version,
        "conteneur": conteneur,
        "libelle": libelle
    }

def _strip_accents(s: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    ).lower()

def _dernier_ligne_non_vide(txt: str) -> str:
    for line in reversed(txt.splitlines()):
        if line.strip():
            return line[:200]
    return ""

def _run(
    cmd: List[str],
    cwd: Path,
    timeout: float,
    env: Mapping[str, str] | None = None,
) -> Tuple[int, str, str]:
    """Exécute une commande et renvoie (code, stdout, stderr)."""
    try:
        cp = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        return cp.returncode, cp.stdout, cp.stderr
    except (subprocess.SubprocessError, OSError) as exc:
        return -1, "", str(exc)

def _test_f1(copie: Path, cwd: Path, délai: float) -> Mapping[str, Any]:
    code, out, err = _run([sys.executable, str(copie), "--help"], cwd, délai)
    ok = code == 0 and out.strip()
    return {
        "code": "F1",
        "intitule": "F1 AIDE",
        "verdict": "REUSSI" if ok else "ECHEU",
        "explication": _dernier_ligne_non_vide(err),
    }

def _test_f2(copie: Path, cwd: Path, délai: float) -> Mapping[str, Any]:
    module_name = copie.stem
    code, out, err = _run(
        [
            sys.executable,
            "-c",
            "import sys, importlib; sys.path.insert(0, sys.argv[1]); importlib.import_module(sys.argv[2])",
            str(cwd),
            module_name,
        ],
        cwd,
        délai,
    )
    ok = code == 0
    return {
        "code": "F2",
        "intitule": "F2 IMPORT",
        "verdict": "REUSSI" if ok else "ECHEU",
        "explication": _dernier_ligne_non_vide(err),
    }

def _json_valide(txt: str) -> Tuple[bool, Any]:
    try:
        data = json.loads(txt)
        return isinstance(data, dict), data
    except Exception:
        return False, None

def _chercher_denominateur(d: Mapping[str, Any]) -> Any:
    cibles = {
        "denominateur",
        "denominator",
        "denom",
        "total",
        "examines",
        "nombre",
        "compte",
    }

    def _search(mapping: Mapping[str, Any]) -> Any:
        for k, v in mapping.items():
            if _strip_accents(k) in cibles:
                return v
        return None

    val = _search(d)
    if val is not None:
        return val
    for sub in ("resume", "bilan"):
        submap = d.get(sub)
        if isinstance(submap, Mapping):
            val = _search(submap)
            if val is not None:
                return val
    return None

def _decouvrir_sous_commandes(copie: Path, cwd: Path, delai: float) -> List[str]:
    """Renvoie la liste des sous‑commandes d’un outil, selon les deux voies décrites."""
    code, out, _ = _run([sys.executable, str(copie), "--help"], cwd, delai)
    if code != 0:
        return []
    lines = out.splitlines()
    usage_block = ""
    for i, line in enumerate(lines):
        if line.lower().startswith("usage:"):
            parts = [line.strip()]
            for j in range(i + 1, len(lines)):
                nxt = lines[j]
                if not nxt.strip():
                    break
                parts.append(nxt.strip())
            usage_block = " ".join(parts)
            break
    subs: Set[str] = set()
    m = re.search(r"\{([^}]+)\}", usage_block)
    if m:
        subs.update(s.strip() for s in m.group(1).split(","))
    pos_section = False
    for i, line in enumerate(lines):
        if line.lower().startswith("positional arguments:"):
            pos_section = True
            for j in range(i + 1, len(lines)):
                l = lines[j].strip()
                if not l:
                    break
                if l.startswith("{") and l.endswith("}"):
                    inner = l[1:-1]
                    subs.update(s.strip() for s in inner.split(","))
                    break
            break
    return sorted(subs)

def _extraire_usage(copie: Path, cwd: Path, delai: float) -> str:
    """Extrait le bloc d'usage complet (ligne usage + suivantes jusqu'à ligne vide)."""
    code, out, _ = _run([sys.executable, str(copie), "--help"], cwd, delai)
    if code != 0:
        return ""
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if line.lower().startswith("usage:"):
            parts = [line.strip()]
            for j in range(i + 1, len(lines)):
                nxt = lines[j]
                if not nxt.strip():
                    break
                parts.append(nxt.strip())
            return " ".join(parts)
    return ""

def _generer_options_obligatoires(usage: str) -> List[Tuple[str, str]]:
    """Parse le bloc d'usage et retourne les options obligatoires avec valeurs plausibles."""
    opts: List[Tuple[str, str]] = []

    # Option hors crochets : --nom VALEUR
    pattern_opt = re.compile(r"(?<!\[)(--\w+)\s+([A-Z_]+)(?![\]\}])")
    for m in pattern_opt.finditer(usage):
        opt, val = m.group(1), m.group(2)
        opts.append((opt, _valeur_par_defaut(val, opt)))

    # Groupe entre parenthèses : (--a A | --b B)
    pattern_group = re.compile(r"\(([^)]+)\)")
    for m in pattern_group.finditer(usage):
        group = m.group(1)
        first = group.split("|")[0].strip()
        m2 = re.match(r"(--\w+)\s+([A-Z_]+)", first)
        if m2:
            opt, val = m2.group(1), m2.group(2)
            opts.append((opt, _valeur_par_defaut(val, opt)))
    return opts

def _valeur_par_defaut(valeur: str, opt: str) -> str:
    """Retourne une valeur plausible selon les règles décrites."""
    mapping = {
        "FICHIER": "temoin.txt",
        "SOURCE": "temoin.txt",
        "MODULE": "temoin.txt",
        "CIBLE": "temoin.txt",
        "ENTREE": "temoin.txt",
        "DOSSIER": "tmpdir",
        "RACINE": "tmpdir",
        "REPERTOIRE": "tmpdir",
        "ARBRE": "tmpdir",
        "PROJET": "tmpdir",
        "PROMPT": "temoin",
        "TEXTE": "temoin",
        "MESSAGE": "temoin",
        "MOTIF": "temoin",
        "REQUETE": "temoin",
        "N": "1",
        "NOMBRE": "1",
        "SECONDES": "1",
        "JETONS": "1",
        "TIMEOUT": "1",
        "TAILLE": "1",
        "LIMITE": "1",
        "URL": "http://127.0.0.1:1",
        "ADRESSE": "http://127.0.0.1:1",
    }
    key = valeur.upper()
    return mapping.get(key, "temoin.txt")

def _premier_positionnel_nom(
    copie: Path,
    cwd: Path,
    delai: float
) -> str | None:
    """Retourne le nom du premier argument positionnel, ou None s’il n’est pas trouvé."""
    code, out, _ = _run([sys.executable, str(copie), "--help"], cwd, delai)
    if code != 0:
        return None
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if line.lower().startswith("positional arguments:"):
            for j in range(i + 1, len(lines)):
                l = lines[j].strip()
                if not l:
                    break
                return l.split()[0]
            break
    return None

def _extraire_section_docstring(
    doc: str,
    titre: str,
) -> List[str]:
    """
    Extrait les lignes d'une section spécifique du docstring.
    La capture s'arrête à une ligne vide ou à un autre intitulé.
    Reconnaît les deux formes d'intitulé (avec ou sans deux-points).
    """
    INTITULES = {'QUESTION', 'MESURE', 'HYPOTHESES', 'HYPOTHÈSES', 'LIMITES',
                 'CONTRE-EXEMPLES', 'CONTRE-EXEMPLE', 'INVOCATION', 'DOMAINE'}
    lignes = []
    capture = False
    for line in doc.splitlines():
        stripped = line.strip()
        haut = stripped.upper()
        if haut == titre.upper() or haut.startswith((titre.upper() + ':', titre.upper() + ' ',
                                                     titre.upper() + '	')):
            capture = True
            # Trois formes d'intitulé : seul sur sa ligne, « TITRE: cmd », « TITRE    cmd ».
            # On ne coupe JAMAIS sur le premier deux-points du reste : une commande
            # peut en porter un ({fichier}:ma_fonction, C:\chemin).
            reste = stripped[len(titre):].lstrip(' 	')
            if reste.startswith(':'):
                reste = reste[1:].strip()
            if reste:
                lignes.append(reste)
            continue
        if capture:
            if not stripped:
                break
            tete = stripped.split(':', 1)[0].split()[0].upper() if stripped else ''
            if tete in INTITULES:
                break
            lignes.append(stripped)
    return lignes

def _recolle_lignes_continuation(lignes: List[str]) -> List[str]:
    """Recolle les lignes se terminant par un antislash avec la suivante."""
    result = []
    i = 0
    while i < len(lignes):
        line = lignes[i]
        while line.endswith('\\') and i + 1 < len(lignes):
            line = line[:-1] + lignes[i+1]
            i += 1
        result.append(line)
        i += 1
    return result

def _ligne_est_commande(ligne: str) -> bool:
    """Vérifie si une ligne ressemble à une commande valide."""
    return "{outil}" in ligne or ligne.strip().startswith(("/", "./", "../", "~"))

def _extraire_invocations_declarees(
    copie: Path,
    cwd: Path,
    piece_valide: Path,
) -> List[List[str]]:
    """
    Lit le docstring du module et extrait les lignes sous le titre INVOCATION.
    Retourne une liste de commandes (listes d'arguments) déjà résolues, incluant
    le chemin complet de l'outil en tête.
    """
    try:
        src = copie.read_text(encoding="utf-8")
    except Exception:
        return []

    # Recherche du docstring triple quotes au début du fichier
    m = re.search(r'"""(.*?)"""', src, re.DOTALL)
    if not m:
        return []
    doc = m.group(1)

    # Extraction des lignes après le titre INVOCATION
    invoc_lines = _extraire_section_docstring(doc, "INVOCATION")
    invoc_lines = _recolle_lignes_continuation(invoc_lines)
    invoc_lines = [line for line in invoc_lines if _ligne_est_commande(line)]

    commands: List[List[str]] = []
    remplacements = {
        '{outil}': str(copie),
        '{fichier}': str(piece_valide),
        '{dossier}': str(cwd),
        '{racine}': str(cwd),
    }

    for line in invoc_lines:
        original_line = line
        # Découpage avec shlex.split avant toute substitution
        try:
            parts = shlex.split(line, posix=True)
        except ValueError as e:
            # Ligne mal formée : on la consigne et on ignore
            continue

        # Remplacement des jetons dans chaque partie
        resolved_parts = []
        for p in parts:
            for jeton, valeur in remplacements.items():
                p = p.replace(jeton, valeur)
            resolved_parts.append(p)

        # Si la ligne ne contenait pas {outil}, on ajoute le chemin de l'outil
        if "{outil}" not in original_line:
            resolved_parts.insert(0, str(copie))

        # Création des chemins sous {dossier} qui n'existent pas encore
        for idx, part in enumerate(resolved_parts):
            if part.startswith(str(cwd)):
                target = Path(part)
                if not target.exists():
                    if "." in target.name:
                        # Fichier : copier le contenu de valide.py
                        target.write_text(piece_valide.read_text(encoding="utf-8"), encoding="utf-8")
                    else:
                        target.mkdir(parents=True, exist_ok=True)
        commands.append(resolved_parts)
    return commands

def _obtenir_json(
    copie: Path,
    cwd: Path,
    délai: float,
    piece_valide: Path,
) -> Tuple[
    Mapping[str, Any] | None,
    List[str] | None,
    bool,
    List[Tuple[List[str], str]],
    Tuple[List[str], str] | None,
    bool,
    List[Dict[str, Any]],
]:
    """
    Exécute l’outil avec les variantes habituelles et les invocations déclarées.
    Retourne le premier JSON exploitable trouvé, la variante utilisée, un flag
    d’inapplicabilité, la liste des tentatives (cmd, dernière erreur),
    éventuellement la tentative légitime de refus, un booléen indiquant si
    l’invocation était déclarée, et la liste des invocations déclarées qui ont
    échoué.
    """
    usage = _extraire_usage(copie, cwd, délai)
    opts_oblig = _generer_options_obligatoires(usage)

    # Détection d'option réseau ou clé API obligatoire
    for opt, _ in opts_oblig:
        nom = opt.lstrip("-").lower()
        if nom in {"cle", "token", "key", "url", "adresse"}:
            return None, None, True, [], None, False, []

    opts_args: List[str] = []
    for opt, val in opts_oblig:
        opts_args.extend([opt, val])

    sous_cmds = _decouvrir_sous_commandes(copie, cwd, délai)

    # Invocations déclarées (priorité)
    declared_cmds = _extraire_invocations_declarees(copie, cwd, piece_valide)
    declared_set = {tuple(cmd) for cmd in declared_cmds}

    variantes: List[List[str]] = []

    # Ajout des variantes déclarées (déjà complètes)
    for cmd in declared_cmds:
        variantes.append(cmd)

    # variantes avec positionnel (guesses)
    variantes.append([str(copie), *opts_args, "--json", str(piece_valide)])
    variantes.append([str(copie), *opts_args, str(piece_valide), "--json"])
    variantes.append([str(copie), *opts_args, "--json", str(cwd)])
    variantes.append([str(copie), *opts_args, str(cwd), "--json"])
    variantes.append([str(copie), *opts_args, "--json"])

    for sub in sous_cmds:
        variantes.append([str(copie), *opts_args, "--json", sub])
        variantes.append([str(copie), *opts_args, sub, "--json"])
        variantes.append([str(copie), *opts_args, "--racine", str(cwd), "--json", sub])
        variantes.append([str(copie), *opts_args, "--json", sub, str(piece_valide)])
        variantes.append([str(copie), *opts_args, sub, str(piece_valide), "--json"])
        variantes.append([str(copie), *opts_args, "--json", sub, str(cwd)])
        variantes.append([str(copie), *opts_args, sub, str(cwd), "--json"])
        variantes.append(
            [str(copie), *opts_args, "--racine", str(cwd), "--json", sub, str(piece_valide)]
        )
        variantes.append(
            [
                str(copie),
                *opts_args,
                "--racine",
                str(cwd),
                "--json",
                sub,
                str(cwd / "sortie_juge"),
            ]
        )

    attempts: List[Tuple[List[str], str]] = []
    legit_refusal: Tuple[List[str], str] | None = None
    declared_failures: List[Dict[str, Any]] = []

    for cmd in variantes:
        code, out, err = _run([sys.executable] + cmd, cwd, délai)
        ok_json, data = _json_valide(out)
        is_declared = tuple(cmd) in declared_set

        # Vérification du refus légitime (avant toute autre logique)
        if (
            code == 3
            and "Traceback" not in err
            and any(
                word in _strip_accents(err.lower())
                for word in ("nul", "vide", "aucun", "refuse", "impossible de conclure")
            )
            and ("denominateur" in _strip_accents(err.lower()) or "dénominateur" in _strip_accents(err.lower()))
        ):
            legit_refusal = (cmd, _dernier_ligne_non_vide(err))

        # Cas où le stdout est un JSON syntaxiquement valide
        if ok_json and "Traceback" not in err:
            denom = _chercher_denominateur(data)
            if isinstance(denom, int) and denom > 0:
                # JSON exploitable : on retourne immédiatement
                declared_flag = is_declared
                return data, cmd, False, attempts, None, declared_flag, declared_failures
            # JSON valide mais pas exploitable : on consigne l'erreur
            if denom == 0:
                err_msg = "denominateur = 0"
            else:
                err_msg = f"JSON sans denominateur exploitable : cles = {list(data.keys())}"
            attempts.append((cmd, err_msg))
            if is_declared:
                declared_failures.append(
                    {
                        "commande": " ".join(cmd),
                        "code": code,
                        "erreur": err_msg,
                    }
                )
            continue

        # Cas d'échec (JSON invalide ou traceback)
        err_msg = _dernier_ligne_non_vide(err)
        attempts.append((cmd, err_msg))
        if is_declared:
            declared_failures.append(
                {
                    "commande": " ".join(cmd),
                    "code": code,
                    "erreur": err_msg if err_msg else "ligne d'invocation mal formée",
                }
            )

    # Aucun JSON exploitable trouvé
    return None, None, False, attempts, legit_refusal, False, declared_failures

def _test_f3_f4(
    copie: Path,
    cwd: Path,
    délai: float,
    piece_valide: Path,
) -> Tuple[
    Mapping[str, Any],
    Mapping[str, Any],
    Any,
    List[str] | None,
    bool,
    List[Tuple[List[str], str]],
    Tuple[List[str], str] | None,
    List[Dict[str, Any]],
]:
    try:
        json_obj, invocation, inapplicable, attempts, legit_refusal, declared, declared_failures = _obtenir_json(
            copie, cwd, délai, piece_valide
        )
    except (ValueError, TypeError) as exc:
        # Défaut du juge : on rattrape et on marque comme NON EPROUVE avec cause spécifique
        f3 = {
            "code": "F3",
            "intitule": "F3 TRAVAIL",
            "verdict": "NON EPROUVE",
            "explication": f"Défaut du juge : {type(exc).__name__} - {str(exc)}",
        }
        f4 = {
            "code": "F4",
            "intitule": "F4 DÉNOMINATEUR",
            "verdict": "NON EPROUVE",
            "explication": "Défaut du juge lors de l'exécution de F3",
        }
        return f3, f4, None, None, False, [], None, []

    # Gestion du cas INAPPLICABLE (réseau ou clé API obligatoire)
    if inapplicable:
        f3 = {
            "code": "F3",
            "intitule": "F3 TRAVAIL",
            "verdict": "INAPPLICABLE",
            "explication": "Option obligatoire nécessitant réseau ou clé API",
            "invocation_reussie": "",
        }
        f4 = {
            "code": "F4",
            "intitule": "F4 DÉNOMINATEUR",
            "verdict": "INAPPLICABLE",
            "explication": "INAPPLICABLE car F3 INAPPLICABLE",
        }
        return f3, f4, None, None, True, attempts, legit_refusal, declared_failures

    # Gestion du refus légitime de conclure (règle 10 en isolement)
    if legit_refusal is not None:
        f3 = {
            "code": "F3",
            "intitule": "F3 TRAVAIL",
            "verdict": "INAPPLICABLE",
            "explication": "refus legitime de conclure : rien a examiner en isolation",
            "invocation_reussie": "",
        }
        f4 = {
            "code": "F4",
            "intitule": "F4 DÉNOMINATEUR",
            "verdict": "INAPPLICABLE",
            "explication": "refus legitime de conclure : rien a examiner en isolation",
        }
        return f3, f4, None, None, False, attempts, legit_refusal, declared_failures

    f3_ok = json_obj is not None
    f3 = {
        "code": "F3",
        "intitule": "F3 TRAVAIL",
        "verdict": "REUSSI" if f3_ok else "ECHEU",
        "explication": "" if f3_ok else "Pas de JSON valide",
        "invocation_reussie": (
            [Path(invocation[0]).name] + invocation[1:] if invocation else []
        ),
        "invocation_declaree": declared,
    }

    denom = _chercher_denominateur(json_obj) if json_obj else None
    f4_ok = denom is not None and denom != 0
    f4_exp = ""
    if not f4_ok:
        if isinstance(json_obj, dict):
            f4_exp = f"Clé de dénominateur manquante ou nulle – clés présentes : {list(json_obj.keys())}"
        else:
            f4_exp = "Clé de dénominateur manquante ou nulle"
    f4 = {
        "code": "F4",
        "intitule": "F4 DÉNOMINATEUR",
        "verdict": "REUSSI" if f4_ok else "ECHEU",
        "explication": f4_exp,
    }
    return f3, f4, denom, invocation, False, attempts, legit_refusal, declared_failures

def _test_reverse_specific(
    copie: Path,
    cwd: Path,
    délai: float,
    cible: Path | str,
    code_str: str,
    env: Mapping[str, str],
) -> Mapping[str, Any]:
    """Teste un rejet (reverse) et renvoie le résultat avec le code fourni."""
    # K2 – détection d’un positionnel non path‑like
    if code_str in {"R1", "R2", "R3"}:
        pos_name = _premier_positionnel_nom(copie, cwd, délai)
        if pos_name and not any(fr in pos_name.lower() for fr in (
            "chemin", "cible", "fichier", "dossier", "path", "file", "dir",
            "source", "racine", "repertoire", "depot", "entree", "sortie",
            "arbre", "archive"
        )):
            return {
                "code": code_str,
                "intitule": "REVERSE",
                "verdict": "INAPPLICABLE",
                "explication": f"positionnel non path-like : {pos_name} -- les contrôles de chemin sont hors sujet",
            }

    if code_str == "R4":
        cmd = [str(copie), "--argument-inconnu"]
    else:
        cmd = [str(copie), str(cible)]

    sous_cmds = _decouvrir_sous_commandes(copie, cwd, délai)
    variantes: List[List[str]] = [cmd]
    for sub in sous_cmds:
        variantes.append([str(copie), "--json", sub, str(cible)])
        variantes.append([str(copie), sub, str(cible), "--json"])

    ok = False
    last_err = ""
    last_code = 0
    for cmd_variant in variantes:
        code, out, err = _run([sys.executable] + cmd_variant, cwd, délai, env=env)
        if code != 0 and "Traceback" not in err:
            ok = True
            break
        last_err = _dernier_ligne_non_vide(err)
        last_code = code

    if ok:
        verdict = "REUSSI"
        explication = ""
    else:
        if not last_err:
            last_err = f"aucun message sur stderr, code {last_code}"
        else:
            last_err = f"code {last_code}, {last_err}"
        verdict = "ECHEU"
        explication = last_err

    return {
        "code": code_str,
        "intitule": "REVERSE",
        "verdict": verdict,
        "explication": explication,
    }

def _test_f5(
    copie: Path,
    cwd: Path,
    délai: float,
    piece_valide: Path,
    markers: Set[str],
    marker_prefixes: Tuple[str, ...],
    denom: Any,
    invocation: List[str] | None,
) -> Mapping[str, Any]:
    """Détecte les fabrications (contrôle F5)."""
    try:
        json_obj, _, _, _, _, _, _ = _obtenir_json(copie, cwd, délai, piece_valide)
    except (ValueError, TypeError) as exc:
        return {
            "code": "F5",
            "intitule": "F5 ANCRAGE",
            "verdict": "NON EPROUVE",
            "explication": f"Défaut du juge : {type(exc).__name__} - {str(exc)}",
        }

    if invocation is None or piece_valide.name not in invocation[1:]:
        return {
            "code": "F5",
            "intitule": "F5 ANCRAGE",
            "verdict": "INAPPLICABLE",
            "explication": "Invocation réussie ne passe pas le fichier témoin",
        }

    if json_obj is None:
        return {
            "code": "F5",
            "intitule": "F5 ANCRAGE",
            "verdict": "INAPPLICABLE",
            "explication": "Aucun JSON exploitable",
        }

    strings: List[str] = []
    for k, v in json_obj.items():
        if isinstance(v, str):
            strings.append(v)
        elif isinstance(v, dict):
            for vv in v.values():
                if isinstance(vv, str):
                    strings.append(vv)

    identifiants: Set[str] = set()
    for s in strings:
        identifiants.update(re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", s))

    fabrication = False
    explications: List[str] = []

    for ident in identifiants:
        if any(ident.startswith(p) for p in marker_prefixes):
            if ident not in markers:
                fabrication = True
                explications.append(f"Fabrication d'identifiant : {ident}")

    for s in strings:
        if any(p in s for p in marker_prefixes):
            if s not in markers:
                fabrication = True
                explications.append(f"Fabrication de chemin : {s}")

    if denom not in (None, 0):
        if not any(m in identifiants or m in strings for m in markers):
            fabrication = True
            explications.append("Dénominateur positif mais aucun marqueur présent")

    if fabrication:
        return {
            "code": "F5",
            "intitule": "F5 ANCRAGE",
            "verdict": "ECHEU",
            "explication": "; ".join(explications),
        }

    if any(m in identifiants or m in strings for m in markers):
        return {
            "code": "F5",
            "intitule": "F5 ANCRAGE",
            "verdict": "REUSSI",
            "explication": "",
        }

    return {
        "code": "F5",
        "intitule": "F5 ANCRAGE",
        "verdict": "ECHEU",
        "explication": "Aucun marqueur trouvé dans la sortie",
    }

def _creer_sitecustomize(mouchard_dir: Path, journal_path: Path) -> None:
    """Écrit le sitecustomize qui loggue les accès (open, io.open, fdopen, socket, subprocess)."""
    site_path = mouchard_dir / "sitecustomize.py"
    code_template = """import builtins
import io
import os
import pathlib
import socket
import subprocess

_JOURNAL_PATH = __CHEMIN_DU_JOURNAL__
_original_open = builtins.open
_JOURNAL_HANDLE = _original_open(_JOURNAL_PATH, "a", encoding="utf-8", errors="replace")

def _log(ligne: str) -> None:
    try:
        _JOURNAL_HANDLE.write(ligne + "\\n")
        _JOURNAL_HANDLE.flush()
    except Exception:
        pass

# builtins.open – signature exacte
_original_builtin_open = builtins.open
def _builtin_open_wrapper(file, mode="r", buffering=-1, encoding=None, errors=None,
                         newline=None, closefd=True, opener=None):
    _log("OPEN\\t{}\\t{}".format(mode, pathlib.Path(file).resolve()))
    return _original_builtin_open(file, mode, buffering, encoding, errors,
                                 newline, closefd, opener)
builtins.open = _builtin_open_wrapper

# io.open – même signature que builtins.open
_original_io_open = io.open
def _io_open_wrapper(file, mode="r", buffering=-1, encoding=None, errors=None,
                     newline=None, closefd=True, opener=None):
    _log("IOOPEN\\t{}\\t{}".format(mode, pathlib.Path(file).resolve()))
    return _original_io_open(file, mode, buffering, encoding, errors,
                             newline, closefd, opener)
io.open = _io_open_wrapper

# io.open_code – un seul argument
_original_io_open_code = io.open_code
def _io_open_code_wrapper(path):
    _log("IOOPEN_CODE\\trb\\t{}".format(pathlib.Path(path).resolve()))
    return _original_io_open_code(path)
io.open_code = _io_open_code_wrapper

# os.fdopen – signature exacte, mais accepte *args/**kwargs pour robustesse
_original_fdopen = os.fdopen
def _fdopen_wrapper(fd, *args, **kwargs):
    mode = args[0] if args else kwargs.get('mode', 'r')
    try:
        path = pathlib.Path("/proc/self/fd/{}".format(fd)).readlink()
    except Exception:
        path = "fd:{}".format(fd)
    _log("FDFOPEN\\t{}\\t{}".format(mode, path))
    return _original_fdopen(fd, *args, **kwargs)
os.fdopen = _fdopen_wrapper

# os.open – signature exacte avec argument nommé
_original_os_open = os.open
def _os_open_wrapper(path, flags, mode=0o777, *, dir_fd=None):
    _log("OSOPEN\\t{}\\t{}".format(flags, pathlib.Path(path).resolve()))
    return _original_os_open(path, flags, mode, dir_fd=dir_fd)
os.open = _os_open_wrapper

# socket.socket – on loggue puis bloque
_original_socket = socket.socket
class _SocketWrapper(socket.socket):
    def __init__(self, *a, **kw):
        family = a[0] if a else socket.AF_INET
        typ = a[1] if len(a) > 1 else socket.SOCK_STREAM
        _log("SOCKET\\t{}\\t{}".format(family, typ))
        raise PermissionError("reseau interdit pendant le reverse test")
socket.socket = _SocketWrapper

# subprocess.Popen – loggue la commande puis passe
_original_popen = subprocess.Popen
class _PopenMouchard(_original_popen):
    def __init__(self, *a, **kw):
        try:
            _log("PROCESS\\t{}".format(a[0] if a else ""))
        except Exception:
            pass
        super().__init__(*a, **kw)
subprocess.Popen = _PopenMouchard
"""
    code = code_template.replace('__CHEMIN_DU_JOURNAL__', repr(str(journal_path)))
    site_path.write_text(code, encoding="utf-8")

def _analyse_journal(
    journal_path: Path,
    temp_dir: Path,
    allowed_extra: List[Path],
) -> Tuple[List[Tuple[Path, str]], List[Tuple[int, int]]]:
    """Retourne (fuites, sockets) à partir du journal."""
    fuites: List[Tuple[Path, str]] = []
    sockets: List[Tuple[int, int]] = []
    if not journal_path.is_file():
        return fuites, sockets
    stdlib = Path(sysconfig.get_paths()["stdlib"]).resolve()
    prefixes = {
        temp_dir.resolve(),
        Path(sys.prefix).resolve(),
        Path(sys.base_prefix).resolve(),
        stdlib,
    }
    prefixes.update(p.resolve() for p in allowed_extra)

    for line in journal_path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        typ = parts[0]
        if typ in ("OPEN", "IOOPEN", "IOOPEN_CODE", "OSOPEN", "FDFOPEN"):
            mode = parts[1]
            chemin = Path(parts[2]).resolve()
            allowed = any(
                chemin == p or chemin.is_relative_to(p) for p in prefixes
            ) or chemin.suffix == ".pyc" or "__pycache__" in str(chemin)
            if not allowed:
                fuites.append((chemin, mode))
        elif typ == "SOCKET":
            try:
                fam = int(parts[1])
                typ_sock = int(parts[2])
                sockets.append((fam, typ_sock))
            except Exception:
                pass
    return fuites, sockets

def _sentinel_intact(sentinel_dir: Path, original_hash: str, expected_len: int) -> bool:
    sentinel = sentinel_dir / "sentinel.txt"
    if not sentinel.is_file():
        return False
    if sentinel.stat().st_size != expected_len:
        return False
    cur_hash = hashlib.sha256(sentinel.read_bytes()).hexdigest()
    return hmac.compare_digest(cur_hash, original_hash)

def _positive_control() -> Mapping[str, Any]:
    """Exécute le contrôle positif du mouchard et renvoie le résultat structuré."""
    ctrl_dir = Path(tempfile.mkdtemp())
    journal_path = Path(tempfile.mktemp())
    _creer_sitecustomize(ctrl_dir, journal_path)

    external_path = Path(
        "C:/Windows/win.ini" if os.name == "nt" else "/etc/hosts"
    )
    if not external_path.is_file():
        return {
            "etat": "NON MESURABLE",
            "chemin_vu": None,
            "code_temoin": 1,
            "raison": "fichier externe introuvable",
        }

    # --- premier témoin (lecture) ---
    script_path = ctrl_dir / "probe.py"
    chemin_litteral = repr(str(external_path))
    lignes = [
        "from pathlib import Path",
        f"Path({chemin_litteral}).read_text(encoding='utf-8', errors='replace')",
    ]
    script_path.write_text("\n".join(lignes) + "\n", encoding="utf-8")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ctrl_dir)
    env["JOURNAL_FUITES"] = str(journal_path)

    code, out, err = _run([sys.executable, str(script_path)], Path.cwd(), 10.0, env=env)

    seen = False
    entrees_journal = 0
    if journal_path.is_file():
        journal_content = journal_path.read_text(encoding="utf-8", errors="replace")
        lignes_journal = journal_content.splitlines()
        entrees_journal = len(lignes_journal)
        for line in lignes_journal:
            parts = line.split("\t")
            if not parts:
                continue
            typ = parts[0]
            if typ in ("OPEN", "IOOPEN", "IOOPEN_CODE", "OSOPEN", "FDFOPEN"):
                mode = parts[1]
                chemin = Path(parts[2]).resolve()
                if chemin == external_path.resolve() and "r" in mode:
                    seen = True
                    break

    # --- second témoin (contrôle positif strict) ---
    second_path = ctrl_dir / "probe2.py"
    lignes2 = [
        "import argparse, json, subprocess, asyncio",
        "class _Sous(subprocess.Popen):",
        "    pass",
        "print('ok')",
    ]
    second_path.write_text(chr(10).join(lignes2) + chr(10), encoding="utf-8")
    code2, out2, err2 = _run([sys.executable, str(second_path)], Path.cwd(), 10.0, env=env)

    # Nettoyage du répertoire de contrôle
    shutil.rmtree(ctrl_dir, ignore_errors=True)
    try:
        journal_path.unlink()
    except Exception:
        pass

    # Analyse du résultat
    if not seen:
        # Le mouchard n'a pas vu le fichier externe → AVEUGLE
        return {
            "etat": "AVEUGLE",
            "chemin_vu": None,
            "code_temoin": 1,
            "raison": "le mouchard n'a pas détecté la lecture du fichier externe",
        }

    if code != 0 or err.strip():
        # Problème avec le premier témoin → NON MESURABLE
        raison = f"premier temoin échoué : {err.strip() or _dernier_ligne_non_vide(err)}"
        return {
            "etat": "NON MESURABLE",
            "chemin_vu": None,
            "code_temoin": 1,
            "raison": raison,
        }

    if code2 != 0 or out2.strip() != "ok":
        # Le second témoin ne s'exécute pas correctement → NON MESURABLE
        raison = f"temoin de controle positif échoué : {err2.strip() or _dernier_ligne_non_vide(err2)}"
        return {
            "etat": "NON MESURABLE",
            "chemin_vu": None,
            "code_temoin": 1,
            "raison": raison,
            "temoin_controle_positif": {"code": code2, "sortie": out2.strip()},
        }

    # Tout est OK → ARME
    return {
        "etat": "ARME",
        "chemin_vu": str(external_path),
        "code_temoin": 0,
        "entrees_journal": entrees_journal,
        "temoin_controle_positif": {"code": 0, "sortie": "ok"},
    }

def eprouver_isolement(chemin_outil: Path, delai: float, controle_positif: Mapping[str, Any]) -> Mapping[str, Any]:
    """Exécute les huit contrôles sur une copie isolée de l’outil."""
    tmp_dir = Path(tempfile.mkdtemp())
    mouchard_dir: Path | None = None
    sentinel_dir: Path | None = None
    incoherences: List[str] = []
    try:
        copie = tmp_dir / chemin_outil.name
        shutil.copy2(chemin_outil, copie)

        suffix = uuid.uuid4().hex[:8]
        func_marker = f"fonction_temoin_{suffix}"
        var_marker = f"variable_temoin_{suffix}"
        file_marker = f"temoin_{suffix}.py"
        markers_set = {func_marker, var_marker, file_marker}
        marker_prefixes = ("fonction_temoin_", "variable_temoin_", "temoin_")

        piece_valide = tmp_dir / "valide.py"
        piece_valide_content = f'''"""Module de test – contrat mesuré.

QUESTION      Exemple de question
MESURE        Exemple de mesure
HYPOTHÈSES    Exemple d'hypothèses
LIMITES       Exemple de limites
CONTRE-EXEMPLES  Exemple de contre‑exemple
DOMAINE       Exemple de domaine
"""

import os
import sys

SOME_CONSTANT = 42

def fonction_exemple(param: int) -> int:
    """Fonction d'exemple.

    >>> fonction_exemple(2)
    4
    """
    return param * 2

def _fonction_privee():
    pass  # jamais appelée

class ExempleClasse:
    """Classe d'exemple."""
    def methode(self, valeur: str) -> str:
        return valeur.upper()

def fabrique_fermeture(prefix: str):
    def fermeture(suffix: str) -> str:
        return prefix + "_" + suffix
    return fermeture

try:
    _ = int("non_int")
except ValueError:
    pass
'''
        piece_valide.write_text(piece_valide_content, encoding="utf-8")

        second_file = tmp_dir / "secondaire.py"
        second_file.write_text("# second fichier pour l'arbre\n", encoding="utf-8")

        sous_dossier = tmp_dir / "sous_dossier"
        sous_dossier.mkdir()
        third_file = sous_dossier / "troisieme.py"
        third_file.write_text("# troisième fichier dans sous‑dossier\n", encoding="utf-8")

        piece_binaire = tmp_dir / "binaire.py"
        contenu = (
            b'\x7fELF\x02\x01\x01\x00'      # en-tête ELF, reconnaissable
            + bytes([0]) * 16               # octets NULS, en quantité
            + b'\xff\xfe\xfd\xfc' * 32      # séquences UTF-8 INVALIDES
            + bytes(range(256)) * 2         # tous les octets possibles
        )
        piece_binaire.write_bytes(contenu)

        dossier_faux = tmp_dir / "dossier_faux"
        dossier_faux.mkdir()

        cible_inexistante = tmp_dir / "inexistant.py"

        f1 = _test_f1(copie, tmp_dir, delai)
        f2 = _test_f2(copie, tmp_dir, delai)
        try:
            f3, f4, denom, invocation, f3_inapp, attempts, legit_refusal, declared_failures = _test_f3_f4(
                copie, tmp_dir, delai, piece_valide
            )
        except (ValueError, TypeError) as exc:
            # Défaut du juge : on rattrape et on marque comme NON EPROUVE avec cause spécifique
            f3 = {
                "code": "F3",
                "intitule": "F3 TRAVAIL",
                "verdict": "NON EPROUVE",
                "explication": f"Défaut du juge : {type(exc).__name__} - {str(exc)}",
            }
            f4 = {
                "code": "F4",
                "intitule": "F4 DÉNOMINATEUR",
                "verdict": "NON EPROUVE",
                "explication": "Défaut du juge lors de l'exécution de F3",
            }
            f5 = {
                "code": "F5",
                "intitule": "F5 ANCRAGE",
                "verdict": "NON EPROUVE",
                "explication": "Défaut du juge lors de l'exécution de F3/F4",
            }
            f3_inapp = False
            denom = None
            invocation = None
            attempts = []
            legit_refusal = None
            declared_failures = []

        mouchard_dir = Path(tempfile.mkdtemp())
        journal_path = Path(tempfile.mktemp())
        _creer_sitecustomize(mouchard_dir, journal_path)

        sentinel_dir = Path(tempfile.mkdtemp())
        sentinel_file = sentinel_dir / "sentinel.txt"
        sentinel_file.write_bytes(SENTINELLE_CONTENU)
        sentinel_hash = hashlib.sha256(SENTINELLE_CONTENU).hexdigest()

        env_base = os.environ.copy()
        env_base["PYTHONPATH"] = str(mouchard_dir)
        env_base["JOURNAL_FUITES"] = str(journal_path)

        r1 = _test_reverse_specific(copie, tmp_dir, delai, cible_inexistante, "R1", env_base)
        r2 = _test_reverse_specific(copie, tmp_dir, delai, dossier_faux, "R2", env_base)
        r3 = _test_reverse_specific(copie, tmp_dir, delai, piece_binaire, "R3", env_base)
        r4 = _test_reverse_specific(copie, tmp_dir, delai, "--dummy-arg", "R4", env_base)

        allowed_extra = [journal_path]
        fuites, sockets = _analyse_journal(journal_path, tmp_dir, allowed_extra)

        r5_ok = all(
            not any(ch in mode for ch in ("r", "rb")) or Path(p).is_relative_to(tmp_dir)
            for p, mode in fuites
        )
        r5 = {
            "code": "R5",
            "intitule": "R5 AUCUNE FUITE DE LECTURE",
            "verdict": "REUSSI" if r5_ok else "ECHEU",
            "explication": "" if r5_ok else "Fuites de lecture détectées",
        }

        ecriture_fuites = [
            p for p, mode in fuites if any(ch in mode for ch in ("w", "a", "x", "+"))
        ]
        sentinel_ok = _sentinel_intact(sentinel_dir, sentinel_hash, len(SENTINELLE_CONTENU))

        new_files_in_sentinel = [
            p for p in sentinel_dir.iterdir() if p.is_file() and p.name != "sentinel.txt"
        ]

        r6_ok = not ecriture_fuites and sentinel_ok and not new_files_in_sentinel
        parts = []
        if ecriture_fuites:
            parts.extend(str(p) for p in ecriture_fuites)
        if not sentinel_ok:
            cur_hash = hashlib.sha256(sentinel_file.read_bytes()).hexdigest()
            parts.append(
                f"sentinel modifié : original {sentinel_hash}, actuel {cur_hash}"
            )
        if new_files_in_sentinel:
            parts.append(
                f"fichiers inattendus dans le répertoire sentinelle : {[str(p) for p in new_files_in_sentinel]}"
            )
        r6_exp = "; ".join(parts)
        r6 = {
            "code": "R6",
            "intitule": "R6 AUCUNE FUITE D'ÉCRITURE",
            "verdict": "REUSSI" if r6_ok else "ECHEU",
            "explication": r6_exp,
        }

        r7_ok = not sockets
        r7 = {
            "code": "R7",
            "intitule": "R7 AUCUN RÉSEAU",
            "verdict": "REUSSI" if r7_ok else "ECHEU",
            "explication": "" if r7_ok else "Tentative d’accès réseau détectée",
        }

        if f3_inapp:
            f5 = {
                "code": "F5",
                "intitule": "F5 ANCRAGE",
                "verdict": "INAPPLICABLE",
                "explication": "F3 INAPPLICABLE, aucun test F5 possible",
                "invocation_reussie": "",
            }
        else:
            lignes = [func_marker + "=None", var_marker + "=42"]
            (tmp_dir / file_marker).write_text(chr(10).join(lignes) + chr(10), encoding="utf-8")
            f5 = _test_f5(
                copie,
                tmp_dir,
                delai,
                piece_valide,
                markers_set,
                marker_prefixes,
                denom,
                invocation,
            )

        details = [f1, f2, f3, f4, f5, r1, r2, r3, r4, r5, r6, r7]

        forward_controls = [f1, f2, f3, f4, f5]
        reverse_controls = [r1, r2, r3, r4]

        forward_ok = not any(d["verdict"] == "ECHEU" for d in forward_controls)
        reverse_ok = not any(d["verdict"] == "ECHEU" for d in reverse_controls)

        if f1["verdict"] == "ECHEU" and f2["verdict"] == "ECHEU":
            verdict = "BLOQUE"
        elif not forward_ok:
            verdict = "FORWARD KO"
        elif not reverse_ok:
            verdict = "REVERSE KO"
        elif not (r5_ok and r6_ok and r7_ok):
            verdict = "FUITE"
        else:
            verdict = "LIVRABLE"

        # R5..R7 comptent : sans eux, tout verdict FUITE était signalé à tort comme « sans echec ».
        if not any(d["verdict"] == "ECHEU" for d in details) and verdict != "LIVRABLE":
            incoh_msg = f"incoherence : {chemin_outil.name} sans echec mais classe {verdict}"
            print(incoh_msg, file=sys.stderr)
            incoherences.append(incoh_msg)

        reverse_hors_sujet = None
        for r in reverse_controls:
            if r["verdict"] == "INAPPLICABLE" and r["code"] in {"R1", "R2", "R3"}:
                pos_name = _premier_positionnel_nom(copie, tmp_dir, delai)
                reverse_hors_sujet = {
                    "positionnel": pos_name or "",
                    "controles": ["R1", "R2", "R3"],
                }
                break

        fuites_report = None if fuites is None else [{"chemin": str(p), "mode": m} for p, m in fuites]
        reseau_report = [{"famille": f, "type": t} for f, t in sockets]

        report_dict: Dict[str, Any] = {
            "plateforme": _plateforme(),
            "chemin": str(chemin_outil),
            "verdict": verdict,
            "details": details,
            "denominateur": denom,
            "fuites": fuites_report,
            "reseau": reseau_report,
            "incoherences": incoherences,
            "tentatives_f3_f4": [
                {"commande": " ".join(cmd), "erreur": err} for cmd, err in attempts
            ],
            "invocations_declarees_essayees": declared_failures,
            "controle_positif_mouchard": controle_positif,
        }

        if legit_refusal is not None:
            report_dict["refus_legitime"] = {
                "commande": " ".join(legit_refusal[0]),
                "code": 3,
                "stderr": legit_refusal[1],
            }

        if reverse_hors_sujet:
            report_dict["reverse_hors_sujet"] = reverse_hors_sujet

        # Vérification des défauts du juge
        juge_en_defaut = any(
            d["verdict"] == "NON EPROUVE" and "Défaut du juge" in d.get("explication", "")
            for d in details
        )
        if juge_en_defaut:
            report_dict["verdict"] = "NON EPROUVE"
            report_dict["cause"] = "defaut du juge"

        return report_dict

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if mouchard_dir is not None:
            shutil.rmtree(mouchard_dir, ignore_errors=True)
        if sentinel_dir is not None:
            shutil.rmtree(sentinel_dir, ignore_errors=True)

def eprouver_tous(chemins: List[Path], delai: float, controle_positif: Mapping[str, Any]) -> Mapping[str, Any]:
    """Applique l’isolement à tous les outils et agrège les résultats."""
    rapports: List[Mapping[str, Any]] = []
    juge_en_defaut_count = 0

    for p in chemins:
        try:
            rapport = eprouver_isolement(p, delai, controle_positif)
            if rapport.get("cause") == "defaut du juge":
                juge_en_defaut_count += 1
        except Exception as exc:
            rapport = {
                "plateforme": _plateforme(),
                "chemin": str(p),
                "verdict": "NON EPROUVE",
                "cause": "defaut du juge",
                "details": [],
                "denominateur": 0,
                "explication": f"{type(exc).__name__}: {exc}",
                "fuites": None,
                "reseau": [],
                "incoherences": [],
                "tentatives_f3_f4": [],
                "controle_positif_mouchard": controle_positif,
            }
            juge_en_defaut_count += 1
        rapports.append(rapport)

    denom_total = sum(
        r["denominateur"]
        for r in rapports
        if isinstance(r.get("denominateur"), (int, float))
    )

    par_verdict: Dict[str, int] = {}
    for r in rapports:
        v = r["verdict"]
        par_verdict[v] = par_verdict.get(v, 0) + 1

    if juge_en_defaut_count > 0:
        par_verdict["JUGE EN DEFAUT"] = juge_en_defaut_count

    lot_suspect = False
    total = len(rapports)
    if total >= 3:
        for cnt in par_verdict.values():
            if cnt / total > 0.7:
                lot_suspect = True
                print(
                    "tous les outils rendent le même verdict : suspecter l'épreuve avant les outils",
                    file=sys.stderr,
                )
                break

    # Second garde : contrôle unique échouant sur >25 % du lot
    controles_suspects: List[Dict[str, Any]] = []
    for r in rapports:
        echec_codes = [d["code"] for d in r["details"] if d["verdict"] == "ECHEU"]
        if len(echec_codes) == 1:
            code = echec_codes[0]
            controles_suspects.append(code)
    compteur: Dict[str, int] = {}
    for c in controles_suspects:
        compteur[c] = compteur.get(c, 0) + 1
    controles_suspects_report: List[Dict[str, Any]] = []
    for code, nb in compteur.items():
        part = nb / total
        if part > 0.25:
            print(
                f"⚠️ garde de lot : {code} est le seul echec de {nb} outils sur {total} ({part:.0%}).",
                file=sys.stderr,
            )
            controles_suspects_report.append(
                {"code": code, "seuls": nb, "lot": total, "part": part}
            )
    if controles_suspects_report:
        lot_suspect = True

    return {
        "plateforme": _plateforme(),
        "denominateur": denom_total,
        "par_verdict": par_verdict,
        "rapports": rapports,
        "lot_suspect": lot_suspect,
        "controles_suspects": controles_suspects_report,
        "contrat": "Conforme au socle décrit dans SOCLE_OUTILS.md",
    }

def _collect_paths(args: List[str]) -> List[Path]:
    result: List[Path] = []
    for p in args:
        path = Path(p)
        if path.is_dir():
            for f in path.glob("*.py"):
                if f.name.startswith("PLAN_") or f.parts[-2] == "__pycache__":
                    continue
                result.append(f)
        elif path.is_file():
            result.append(path)
    return result

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Éprouve un outil en isolement total selon le socle.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("cibles", nargs="+", help="Fichiers ou dossiers d'outils.")
    parser.add_argument("--racine", type=Path, default=RACINE, help="Chemin racine du projet.")
    parser.add_argument("--delai", type=float, default=60.0, help="Timeout en secondes pour chaque sous‑processus.")
    parser.add_argument("--json", action="store_true", help="Sortie au format JSON.")
    parser.add_argument("--detail", action="store_true", help="Affiche le détail des contrôles échoués.")
    ns = parser.parse_args()

    if not Path(sys.executable).is_file():
        print("Interpréteur Python introuvable.", file=sys.stderr)
        return 5

    controle_positif = _positive_control()
    if controle_positif["etat"] != "ARME":
        if controle_positif["etat"] == "AVEUGLE":
            print(
                "controle positif du mouchard ECHOUE : le detecteur de fuites n est pas arme, aucun verdict n est rendu",
                file=sys.stderr,
            )
            return 4
        else:
            raison = controle_positif.get("raison", "raison inconnue")
            print(
                f"controle positif du mouchard NON MESURABLE : {raison}",
                file=sys.stderr,
            )
            return 5

    chemins = _collect_paths(ns.cibles)
    if not chemins:
        print("Aucun fichier d'outil trouvé.", file=sys.stderr)
        return 2

    resultat = eprouver_tous(chemins, ns.delai, controle_positif)

    if ns.json:
        json.dump(resultat, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        for r in resultat["rapports"]:
            sys.stdout.write(f"{r['chemin']}: {r['verdict']}\n")
            if ns.detail:
                for d in r["details"]:
                    if d["verdict"] == "ECHEU":
                        sys.stdout.write(f"  {d['intitule']}: {d['explication']}\n")
                if r.get("fuites"):
                    sys.stdout.write("  Fuites détectées :\n")
                    for f in r["fuites"]:
                        sys.stdout.write(f"    {f['chemin']} (mode {f['mode']})\n")
                if r.get("reseau"):
                    sys.stdout.write("  Tentatives réseau :\n")
                    for s in r["reseau"]:
                        sys.stdout.write(f"    famille={s['famille']} type={s['type']}\n")
                if r.get("incoherences"):
                    sys.stdout.write("  Incohérences détectées :\n")
                    for inc in r["incoherences"]:
                        sys.stdout.write(f"    {inc}\n")
        if ns.detail:
            sys.stdout.write("\nStatistiques par verdict:\n")
            for v, c in resultat["par_verdict"].items():
                sys.stdout.write(f"  {v}: {c}\n")
            sys.stdout.write(f"Dénominateur total: {resultat['denominateur']}\n")

    # Un outil non éprouvé n'est pas livrable, que la faute soit la sienne ou celle du juge.
    if any(r["verdict"] == "NON EPROUVE" for r in resultat["rapports"]):
        return 4
    if any(r["verdict"] == "INAPPLICABLE" for r in resultat["rapports"]):
        return 2
    if resultat["denominateur"] == 0:
        return 3
    # FUITE manquait ici : un outil qui lisait /etc/hosts hors du bac était classé FUITE
    # et le juge rendait 0 — la CI et `docker build` le laissaient passer.
    if resultat["par_verdict"].get("REVERSE KO", 0) > 0 or resultat["par_verdict"].get("FUITE", 0) > 0:
        return 1
    if resultat["par_verdict"].get("FORWARD KO", 0) > 0 or resultat["par_verdict"].get("BLOQUE", 0) > 0:
        return 2
    # Filet : tout verdict autre que LIVRABLE, même un verdict qu'on n'a pas encore prévu, est un défaut.
    if any(r["verdict"] != "LIVRABLE" for r in resultat["rapports"]):
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())