"""
QUESTION      Puis-je chiffrer ce payload pour le transporter ?
MESURE        Chiffre le payload en blocs de 16 octets avec dérive de clé PBKDF2‑HMAC‑SHA256.
              AES‑256‑CTR si cryptography disponible, sinon HMAC‑SHA256‑CTR en mode dégradé.
HYPOTHESES    Le payload est un fichier local. Le mot de passe est fourni par l'utilisateur.
LIMITES       Le mode dégradé n'est pas un chiffrement standard audité. La sécurité repose sur le mot de passe.
CONTRE-EXEMPLES Payload vide : dénominateur nul, l'outil refuse de conclure.
              Mot de passe vide : refuse.
INVOCATION
    {outil} chiffrer -i {fichier} -m secret --json
DOMAINE       Fichiers locaux sur cette machine.
"""
from __future__ import annotations

import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

import argparse
import base64
import hashlib
import hmac
import json
import secrets
from pathlib import Path
from typing import Any

__all__ = ["chiffrer", "dechiffrer", "valider_source_python"]

MAGIC = b"CP1\x00"
TAILLE_SEL = 16
TAILLE_BLOC = 16
ITERATIONS = 100_000
LIMITE_EXAMINES = 200

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    HAS_CRYPTOGRAPHY = True
except ImportError:
    HAS_CRYPTOGRAPHY = False


def _xor(a: bytes, b: bytes) -> bytes:
    """XOR deux séquences d'octets."""
    return bytes(x ^ y for x, y in zip(a, b))


def deriver_cle(mot_de_passe: str, sel: bytes) -> bytes:
    """Derive une cle de 32 octets depuis le mot de passe et le sel."""
    return hashlib.pbkdf2_hmac("sha256", mot_de_passe.encode("utf-8"), sel, ITERATIONS, dklen=32)


def chiffrer(payload: bytes, mot_de_passe: str) -> dict[str, Any]:
    """Chiffre un payload avec un mot de passe."""
    if not mot_de_passe:
        raise ValueError("Le mot de passe ne peut pas être vide.")
    sel = secrets.token_bytes(TAILLE_SEL)
    cle = deriver_cle(mot_de_passe, sel)

    blocs_info: list[dict[str, int]] = []
    compteur = 0

    if HAS_CRYPTOGRAPHY:
        nonce = secrets.token_bytes(16)
        cipher = Cipher(algorithms.AES(cle), modes.CTR(nonce))
        enc = cipher.encryptor()
        chiffre = enc.update(payload) + enc.finalize()
        for i in range(0, len(payload), TAILLE_BLOC):
            blocs_info.append({"index": compteur, "offset": i, "taille": min(TAILLE_BLOC, len(payload) - i)})
            compteur += 1
        blob = MAGIC + b"A" + sel + nonce + chiffre
        algo = "AES-256-CTR"
        degrade = False
    else:
        parties: list[bytes] = []
        for i in range(0, len(payload), TAILLE_BLOC):
            morceau = payload[i:i + TAILLE_BLOC]
            flux = hmac.new(cle, compteur.to_bytes(8, "big"), hashlib.sha256).digest()
            parties.append(_xor(morceau, flux[:len(morceau)]))
            blocs_info.append({"index": compteur, "offset": i, "taille": len(morceau)})
            compteur += 1
        chiffre = b"".join(parties)
        blob = MAGIC + b"H" + sel + chiffre
        algo = "HMAC-SHA256-CTR (degrade)"
        degrade = True

    return {
        "blob_b64": base64.b64encode(blob).decode("ascii"),
        "algorithme": algo,
        "degrade": degrade,
        "taille": len(payload),
        "blocs": blocs_info,
        "denominateur": compteur,
    }


def dechiffrer(blob_b64: str, mot_de_passe: str) -> dict[str, Any]:
    """Déchiffre un blob base64 avec un mot de passe."""
    if not mot_de_passe:
        raise ValueError("Le mot de passe ne peut pas être vide.")
    blob = base64.b64decode(blob_b64)
    if not blob.startswith(MAGIC):
        raise ValueError("Blob invalide : magic manquant.")
    mode = blob[len(MAGIC) : len(MAGIC) + 1]
    reste = blob[len(MAGIC) + 1 :]
    sel = reste[:TAILLE_SEL]
    corps = reste[TAILLE_SEL:]
    cle = deriver_cle(mot_de_passe, sel)

    blocs_info: list[dict[str, int]] = []
    compteur = 0

    if mode == b"A" and HAS_CRYPTOGRAPHY:
        nonce = corps[:16]
        chiffre = corps[16:]
        cipher = Cipher(algorithms.AES(cle), modes.CTR(nonce))
        dec = cipher.decryptor()
        clair = dec.update(chiffre) + dec.finalize()
        for i in range(0, len(clair), TAILLE_BLOC):
            blocs_info.append({"index": compteur, "offset": i, "taille": min(TAILLE_BLOC, len(clair) - i)})
            compteur += 1
        algo = "AES-256-CTR"
        degrade = False
    elif mode == b"H":
        parties: list[bytes] = []
        for i in range(0, len(corps), TAILLE_BLOC):
            morceau = corps[i:i + TAILLE_BLOC]
            flux = hmac.new(cle, compteur.to_bytes(8, "big"), hashlib.sha256).digest()
            parties.append(_xor(morceau, flux[:len(morceau)]))
            blocs_info.append({"index": compteur, "offset": i, "taille": len(morceau)})
            compteur += 1
        clair = b"".join(parties)
        algo = "HMAC-SHA256-CTR (degrade)"
        degrade = True
    elif mode == b"A" and not HAS_CRYPTOGRAPHY:
        raise ValueError("Blob AES mais cryptography non disponible sur cette machine.")
    else:
        raise ValueError(f"Mode de chiffrement inconnu dans le blob : {mode!r}")

    return {
        "clair": clair,
        "algorithme": algo,
        "degrade": degrade,
        "taille": len(clair),
        "blocs": blocs_info,
        "denominateur": compteur,
    }


def valider_source_python(source: str, nom: str) -> bool:
    """Valide un source Python en le compilant avec compile()."""
    try:
        compile(source, nom, "exec")
        return True
    except SyntaxError:
        return False


def _contrat() -> dict[str, str]:
    """Renvoie le dictionnaire de contrat pour la sortie --json."""
    return {
        "QUESTION": "Puis-je chiffrer ce payload pour le transporter ?",
        "MESURE": "Chiffrement du payload avec dérive de clé par mot de passe (PBKDF2‑HMAC‑SHA256). AES‑256‑CTR si cryptography disponible, sinon HMAC‑SHA256‑CTR en mode dégradé.",
        "HYPOTHESES": "Le payload est un contenu binaire ou textuel. Le mot de passe est fourni par l'utilisateur.",
        "LIMITES": "Le mode dégradé n'est ni AES ni ChaCha20. Il ne remplace pas un chiffrement audité.",
        "CONTRE-EXEMPLES": "Payload vide : dénominateur nul, l'outil refuse de conclure. Mot de passe vide : refuse.",
        "DOMAINE": "Transport de payloads entre agents ou machines via canal non fiable.",
    }


class JSONArgumentParser(argparse.ArgumentParser):
    """ArgumentParser qui renvoie une erreur JSON sur stdout au lieu d'un texte d'usage."""

    def error(self, message: str) -> None:  # pragma: no cover
        err_obj = {"error": message, "denominateur": 0}
        json.dump(err_obj, sys.stdout, ensure_ascii=False)
        self.exit(2)


def _parent_parser() -> argparse.ArgumentParser:
    """Parseur parent contenant les options communes."""
    parent = argparse.ArgumentParser(add_help=False)
    parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Sortie JSON sur stdout",
    )
    parent.add_argument(
        "--racine",
        type=str,
        default=argparse.SUPPRESS,
        help="Racine surchargeant la racine par défaut, insérée en tête de sys.path",
    )
    return parent


def _preparer_analyseur() -> argparse.ArgumentParser:
    """Crée et configure l'analyseur d'arguments."""
    racine_defaut = Path(__file__).resolve().parent
    parent = _parent_parser()
    analyseur = JSONArgumentParser(
        prog="chiffrer_payload.py",
        description="Chiffrer ou déchiffrer un payload pour le transporter.",
        epilog="Exemple:\n  python chiffrer_payload.py chiffrer -i payload.py -m secret",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[parent],
    )
    sous = analyseur.add_subparsers(dest="commande", required=True)

    p_c = sous.add_parser(
        "chiffrer",
        help="Chiffrer un payload",
        parents=[parent],
    )
    p_c.add_argument("--input", "-i", type=str, required=True, help="Chemin du fichier payload")
    p_c.add_argument("--mot-de-passe", "-m", type=str, required=True, help="Mot de passe")

    p_d = sous.add_parser(
        "dechiffrer",
        help="Déchiffrer un payload",
        parents=[parent],
    )
    p_d.add_argument("--input", "-i", type=str, required=True, help="Chemin du fichier chiffré")
    p_d.add_argument("--mot-de-passe", "-m", type=str, required=True, help="Mot de passe")
    p_d.add_argument("--output", "-o", type=str, default=None, help="Fichier de sortie pour le clair")

    return analyseur


def _gerer_racine(args: argparse.Namespace) -> None:
    """Insère --racine en tête de sys.path si fourni."""
    if not hasattr(args, "racine"):
        return
    racine_resolue = str(Path(args.racine).resolve())
    if racine_resolue not in sys.path:
        sys.path.insert(0, racine_resolue)


def _verifier_fichier(chemin: Path) -> tuple[int, str]:
    """Vérifie qu'un fichier existe et est lisible. Retourne (code, message)."""
    if not chemin.exists():
        return 2, f"Erreur : fichier introuvable : {chemin}"
    if not chemin.is_file():
        return 2, f"Erreur : pas un fichier régulier : {chemin}"
    return 0, ""


def _json_error(message: str) -> str:
    """Construit une chaîne JSON d'erreur avec denominateur 0."""
    return json.dumps({"error": message, "denominateur": 0}, ensure_ascii=False)


def _executer_chiffrer(args: argparse.Namespace) -> tuple[int, str | None]:
    """Exécute la sous‑commande chiffrer et renvoie (code, sortie_stdout)."""
    chemin = Path(args.input)
    code, msg = _verifier_fichier(chemin)
    if code != 0:
        return code, _json_error(msg) if getattr(args, "json", False) else None

    try:
        payload = chemin.read_bytes()
    except OSError as e:
        err = f"Erreur : fichier illisible : {e}"
        return 2, _json_error(err) if getattr(args, "json", False) else None

    if chemin.suffix == ".py":
        try:
            source = payload.decode("utf-8")
            if not valider_source_python(source, str(chemin)):
                err = "Erreur : le payload n'est pas du Python valide (compile a échoué)."
                return 1, _json_error(err) if getattr(args, "json", False) else None
        except UnicodeDecodeError:
            err = "Erreur : encodage non utf-8 pour validation Python."
            return 1, _json_error(err) if getattr(args, "json", False) else None

    try:
        resultat = chiffrer(payload, args.mot_de_passe)
    except Exception as e:
        err = f"Erreur : {e}"
        return 2, _json_error(err) if getattr(args, "json", False) else None

    if resultat["denominateur"] == 0:
        sys.stderr.write("Denominateur nul : rien à examiner, refus de conclure.\n")
        return 3, None

    if getattr(args, "json", False):
        obj = {
            "contrat": _contrat(),
            "commande": "chiffrer",
            "denominateur": resultat["denominateur"],
            "examines": resultat["blocs"][:LIMITE_EXAMINES],
            "examines_tronques": len(resultat["blocs"]) > LIMITE_EXAMINES,
            "algorithme": resultat["algorithme"],
            "degrade": resultat["degrade"],
            "taille": resultat["taille"],
            "blob_b64": resultat["blob_b64"],
        }
        sortie = json.dumps(obj, ensure_ascii=False, indent=2)
    else:
        if resultat["degrade"]:
            sys.stderr.write("ATTENTION : mode dégradé (cryptography non disponible). Chiffrement non standard.\n")
        sortie = resultat["blob_b64"]
    return 0, sortie


def _executer_dechiffrer(args: argparse.Namespace) -> tuple[int, str | None]:
    """Exécute la sous‑commande dechiffrer et renvoie (code, sortie_stdout)."""
    chemin = Path(args.input)
    code, msg = _verifier_fichier(chemin)
    if code != 0:
        return code, _json_error(msg) if getattr(args, "json", False) else None

    try:
        blob_b64 = chemin.read_bytes().decode("ascii").strip()
    except OSError as e:
        err = f"Erreur : fichier illisible : {e}"
        return 2, _json_error(err) if getattr(args, "json", False) else None
    except UnicodeDecodeError:
        err = "Erreur : le fichier chiffré n'est pas du texte base64 valide."
        return 2, _json_error(err) if getattr(args, "json", False) else None

    try:
        resultat = dechiffrer(blob_b64, args.mot_de_passe)
    except Exception as e:
        err = f"Erreur : {e}"
        return 2, _json_error(err) if getattr(args, "json", False) else None

    if resultat["denominateur"] == 0:
        sys.stderr.write("Denominateur nul : rien à examiner, refus de conclure.\n")
        return 3, None

    if args.output:
        chemin_sortie = Path(args.output)
        try:
            chemin_sortie.write_bytes(resultat["clair"])
        except OSError as e:
            err = f"Erreur : impossible d'écrire {chemin_sortie} : {e}"
            return 2, _json_error(err) if getattr(args, "json", False) else None
    else:
        chemin_sortie = None

    if getattr(args, "json", False):
        obj = {
            "contrat": _contrat(),
            "commande": "dechiffrer",
            "denominateur": resultat["denominateur"],
            "examines": resultat["blocs"][:LIMITE_EXAMINES],
            "examines_tronques": len(resultat["blocs"]) > LIMITE_EXAMINES,
            "algorithme": resultat["algorithme"],
            "degrade": resultat["degrade"],
            "taille": resultat["taille"],
            "clair_b64": base64.b64encode(resultat["clair"]).decode("ascii"),
        }
        if chemin_sortie is not None:
            obj["fichier_sortie"] = str(chemin_sortie)
        sortie = json.dumps(obj, ensure_ascii=False, indent=2)
    else:
        if resultat["degrade"]:
            sys.stderr.write("ATTENTION : mode dégradé (cryptography non disponible).\n")
        if chemin_sortie is None:
            try:
                sortie = resultat["clair"].decode("utf-8")
            except UnicodeDecodeError:
                sys.stderr.write("(contenu binaire non affichable – utilisez --output ou --json)\n")
                sortie = None
        else:
            sys.stderr.write(f"Clair écrit dans {chemin_sortie}\n")
            sortie = None
    return 0, sortie


def main() -> int:
    """Point d'entrée de la CLI."""
    analyseur = _preparer_analyseur()
    args = analyseur.parse_args()
    _gerer_racine(args)

    if args.commande == "chiffrer":
        code, sortie = _executer_chiffrer(args)
    elif args.commande == "dechiffrer":
        code, sortie = _executer_dechiffrer(args)
    else:
        sys.stderr.write("Erreur : sous-commande inconnue.\n")
        return 2

    if code == 0 and sortie is not None:
        print(sortie)
    return code


if __name__ == "__main__":
    raise SystemExit(main())