#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tracer_llm.py - Outil de détection de traces laissées par un flux LLM

QUESTION      Quelles traces ce flux LLM a-t-il laissées ?
MESURE        Nous examinons les variables d'environnement connues pour être définies par Langfuse ou OpenTelemetry, et, si configuré, les fichiers de traces exportés par l'exportateur fichier d'OpenTelemetry.
HYPOTHÈSES    Le flux LLM a utilisé Langfuse ou OpenTelemetry pour la traçage, et a laissé des traces dans l'environnement (variables d'environnement) ou dans le système de fichiers (si exportateur fichier configuré).
LIMITES       Nous ne détectons pas les traces envoyées à un point de terminaison distant (sans fichier local) si aucune variable d'environnement pertinente n'est définie. Nous ne examinons pas les traces dans les journaux standards ou autres systèmes de stockage.
CONTRE-EXEMPLES Un flux LLM qui utilise Langfuse mais avec toutes les variables d'environnement définies sur des chaînes vides ne serait pas détecté. De même, un exportateur fichier configuré avec un répertoire inaccessible ou sans préfixe correspondant à aucun fichier ne serait pas détecté.
DOMAINE       Cet outil est conçu pour détecter les traces laissées par des flux LLM ayant utilisé Langfuse ou OpenTelemetry dans l'environnement d'exécution actuel.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Reconfiguration de l'encodage pour stdout et stderr
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

__all__ = ["analyser_traces", "main"]


def analyser_traces(racine: Path) -> Tuple[int, List[Dict[str, Any]], bool, bool]:
    """
    Analyse l'environnement à la recherche de traces laissées par un flux LLM.

    Retourne un tuple (denominateur, examines, examines_tronques, trace_trouvee) où :
      - denominateur : nombre d'éléments effectivement examinés
      - examines : liste d'éléments examinés (chaque élément est un dictionnaire avec au moins une clé "nom")
      - examines_tronques : True si la liste a été tronquée à 200 éléments
      - trace_trouvee : True si au moins une trace a été détectée
    """
    # Variables d'environnement à examiner pour Langfuse et OpenTelemetry
    env_vars_to_check: Tuple[str, ...] = (
        # Langfuse
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_BASE_URL",
        "LANGFUSE_HOST",
        "LANGFUSE_TIMEOUT",
        "LANGFUSE_DEBUG",
        "LANGFUSE_TRACING_ENABLED",
        "LANGFUSE_FLUSH_AT",
        "LANGFUSE_FLUSH_INTERVAL",
        "LANGFUSE_ENVIRONMENT",
        "LANGFUSE_RELEASE",
        "LANGFUSE_SAMPLE_RATE",
        # OpenTelemetry général
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_PROTOCOL",
        "OTEL_EXPORTER_OTLP_CERTIFICATE",
        "OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE",
        "OTEL_EXPORTER_OTLP_CLIENT_KEY",
        "OTEL_EXPORTER_OTLP_TIMEOUT",
        "OTEL_EXPORTER_OTLP_INSECURE",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_TRACES_EXPORTER",
        "OTEL_METRICS_EXPORTER",
        "OTEL_LOGS_EXPORTER",
        "OTEL_TRACES_SAMPLER",
        "OTEL_TRACES_SAMPLER_ARG",
        "OTEL_RESOURCE_ATTRIBUTES",
        "OTEL_SERVICE_NAME",
        "OTEL_SERVICE_VERSION",
        "OTEL_PYTHON_ID_GENERATOR",
        "OTEL_PYTHON_TRACER_PROVIDER",
        "OTEL_PYTHON_DISABLED_INSTRUMENTATIONS",
        # Variables spécifiques à l'exportateur fichier OpenTelemetry
        "OTEL_EXPORTER_FILE_DIRECTORY",
        "OTEL_EXPORTER_FILE_PREFIX",
    )

    examines: List[Dict[str, Any]] = []
    trace_trouvee = False

    # 1. Examen des variables d'environnement
    for var_name in env_vars_to_check:
        value = os.environ.get(var_name, "")
        element: Dict[str, Any] = {
            "nom": var_name,
            "valeur": value if value else None,
            "present": bool(value),
        }
        examines.append(element)
        if bool(value):
            trace_trouvee = True

    # 2. Examen des fichiers de trace si l'exportateur fichier est configuré
    otel_traces_exporter = os.environ.get("OTEL_TRACES_EXPORTER", "")
    if otel_traces_exporter == "file":
        directory_str = os.environ.get("OTEL_EXPORTER_FILE_DIRECTORY", ".")
        prefix = os.environ.get("OTEL_EXPORTER_FILE_PREFIX", "otel-traces")
        try:
            directory = Path(directory_str)
            if directory.is_dir():
                # Limiter à 20 fichiers pour éviter de dépasser 200 éléments au total
                files = [
                    f
                    for f in directory.iterdir()
                    if f.is_file() and f.name.startswith(prefix)
                ][:20]
                for f in files:
                    element = {
                        "nom": str(f),
                        "type": "fichier de trace OpenTelemetry",
                        "taille": f.stat().st_size,
                    }
                    examines.append(element)
                    trace_trouvee = True
            # else: répertoire inexistant ou non répertoire → rien de plus
        except Exception as e:  # pragma: no cover - dépend de l'environnement
            print(
                f"Erreur lors de l'accès au répertoire de traces {directory_str} : {e}",
                file=sys.stderr,
            )

    denominateur = len(examines)
    examines_tronques = False
    if denominateur > 200:
        examines = examines[:200]
        examines_tronques = True

    return denominateur, examines, examines_tronques, trace_trouvee


def _build_contrat() -> Dict[str, str]:
    """Construit le dictionnaire du contrat de mesure."""
    return {
        "QUESTION": "Quelles traces ce flux LLM a-t-il laissées ?",
        "MESURE": "Nous examinons les variables d'environnement connues pour être définies par Langfuse ou OpenTelemetry, et, si configuré, les fichiers de traces exportés par l'exportateur fichier d'OpenTelemetry.",
        "HYPOTHESES": "Le flux LLM a utilisé Langfuse ou OpenTelemetry pour la traçage, et a laissé des traces dans l'environnement (variables d'environnement) ou dans le système de fichiers (si exportateur fichier configuré).",
        "LIMITES": "Nous ne détectons pas les traces envoyées à un point de terminaison distant (sans fichier local) si aucune variable d'environnement pertinente n'est définie. Nous ne examinons pas les traces dans les journaux standards ou autres systèmes de stockage.",
        "CONTRE-EXEMPLES": "Un flux LLM qui utilise Langfuse mais avec toutes les variables d'environnement définies sur des chaînes vides ne serait pas détecté. De même, un exportateur fichier configuré avec un répertoire inaccessible ou sans préfixe correspondant à aucun fichier ne serait pas détecté.",
        "DOMAINE": "Cet outil est conçu pour détecter les traces laissées par des flux LLM ayant utilisé Langfuse ou OpenTelemetry dans l'environnement d'exécution actuel.",
    }


def main() -> int:
    """Point d'entrée principal de l'outil."""
    parser = argparse.ArgumentParser(
        description="Détecte les traces laissées par un flux LLM dans l'environnement.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Exemple d'appel :\n  python tracer_llm.py --racine /path/to/project --json",
    )
    parser.add_argument(
        "--racine",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="Répertoire racine du projet (par défaut : répertoire du script)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie au format JSON (un seul objet sur stdout)",
    )
    args = parser.parse_args()

    racine = args.racine.resolve()
    # Insertion du répertoire racine en tête de sys.path si l'outil importe une cible
    # (non applicable ici, mais conservé pour respecter la règle 6)
    if str(racine) not in sys.path:
        sys.path.insert(0, str(racine))

    # Dégradé : vérifier la disponibilité des modules optionnels
    _DEGRADE_MODE = False
    for _module in (
        "langfuse",
        "opentelemetry.sdk",
        "opentelemetry.api",
        "opentelemetry.exporter.otlp.proto.http",
    ):
        try:
            __import__(_module)
        except ImportError:
            _DEGRADE_MODE = True

    if _DEGRADE_MODE:
        print(
            "Mode dégradé : certains modules optionnels ne sont pas disponibles.",
            file=sys.stderr,
        )

    denominateur, examines, examines_tronques, trace_trouvee = analyser_traces(racine)

    if denominateur == 0:
        print(
            "Aucun élément examiné, impossible de conclure.",
            file=sys.stderr,
        )
        return 3

    if args.json:
        sortie = {
            "contrat": _build_contrat(),
            "denominateur": denominateur,
            "examines": examines,
            "examines_tronques": examines_tronques,
        }
        print(json.dumps(sortie, ensure_ascii=False, indent=None))
        return 0 if not trace_trouvee else 1
    else:
        # Sortie lisible par un humain sur stdout
        if trace_trouvee:
            print("Traces détectées :")
            for element in examines:
                if element.get("present") is True:
                    print(f"  - {element['nom']} = {element.get('valeur', '(vide)')}")
                elif element.get("type") == "fichier de trace OpenTelemetry":
                    print(
                        f"  - Fichier de trace : {element['nom']} (taille : {element.get('taille', 0)} octets)"
                    )
        else:
            print("Aucune trace détectée.")

        # Diagnostics sur stderr (en plus du message de mode dégradé éventuel)
        if _DEGRADE_MODE:
            print(
                "Note : certains modules optionnels sont manquants, fonctionnement en mode dégradé.",
                file=sys.stderr,
            )
        return 0 if not trace_trouvee else 1


if __name__ == "__main__":
    raise SystemExit(main())