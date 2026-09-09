![banniere](https://raw.githubusercontent.com/KinSushi/agent-foundry/main/images/banner.png)

![Licence](https://img.shields.io/badge/licence-AGPL--3.0-blue) ![Python](https://img.shields.io/badge/python-3.14-blue) ![Outils](https://img.shields.io/badge/outils-142-brightgreen) ![Prouvés](https://img.shields.io/badge/prouvés%20en%20isolation-142%2F142-brightgreen) ![Plateformes](https://img.shields.io/badge/plateformes-2-brightgreen)

# Agent Foundry

> Increase what AI can produce

142 outils en ligne de commande pour les agents IA. Chacun donne à l'agent une capacité qu'il n'a pas, ou rend meilleur ce qu'il fait mal : refuser de conclure sur rien, détecter une API inventée, dire ce qu'il a réellement examiné.

Bibliothèque standard seule. Aucune dépendance obligatoire. Et rien n'est publié sur parole : **les 142 outils sont prouvés en isolation totale, sur deux plateformes**, par un juge qui se prouve d'abord lui-même.

| Mesure | Valeur | Commande qui la reproduit |
|--------|--------|--------------------------|
| Outils livrés | 142 | `ls outils/*.py \| wc -l` |
| Conformes au socle | 142/142 | `python mesures/porte_qualite.py outils/*.py` |
| Livrables en isolation | 142/142 | `python mesures/test_isolation.py outils` |

**Les 142 outils du dépôt d'origine ont franchi la porte.** Aucun n'a été écarté.

## Démarrage

Aucune installation. Python 3.14, et c'est tout.

```bash
git clone https://github.com/KinSushi/agent-foundry && cd agent-foundry
python outils/afficher_progression.py --help
```

Chaque outil documente ses arguments par `--help`, rend du JSON avec `--json`, et publie `denominateur` — le nombre d'éléments qu'il a réellement examinés. Un outil qui n'a rien à examiner refuse de conclure et le dit.

Trois pour commencer :

| Outil | Ce qu'il répond |
|---|---|
| [afficher_progression](docs/afficher_progression.md) | Où en est ce traitement long ? |
| [afficher_riche](docs/afficher_riche.md) | Comment présenter ce résultat de manière lisible ? |
| [analyser_portees](docs/analyser_portees.md) | À quelle portée appartient chaque nom, et quelles fermetures capturent variable qui évolue ? |

Certains outils font davantage si une bibliothèque tierce est présente, et le disent sur stderr quand elle manque : `pip install .[tout]` (n'installe que les bibliothèques optionnelles ; les outils restent des scripts).

### Reproduire l'audit complet

La construction rejoue la porte de qualité et le juge d'isolation **à l'intérieur de l'image**. Si un seul outil échoue, l'image n'existe pas.

```bash
docker build -t agent-foundry .
```

## Les outils

142 outils, chacun avec sa page : ce qu'il répond, comment on s'en sert, toutes ses options, et ce qu'il ne fait pas.

<details>
<summary>Voir les 142 outils</summary>

| Outil | Ce qu’il répond | Éprouvé sur |
|-------|-----------------|------------|
| [afficher_progression](docs/afficher_progression.md) | Où en est ce traitement long ? | 2 / 2 |
| [afficher_riche](docs/afficher_riche.md) | Comment présenter ce résultat de manière lisible ? | 2 / 2 |
| [analyser_portees](docs/analyser_portees.md) | À quelle portée appartient chaque nom, et quelles fermetures capturent variable qui évolue ? | 2 / 2 |
| [appeler_llm](docs/appeler_llm.md) | Que répond ce fournisseur de LLM ? | 2 / 2 |
| [appeler_natif](docs/appeler_natif.md) | Comment étendre Python avec du code C de manière sûre et dynamique ? | 2 / 2 |
| [architecture](docs/architecture.md) | par quoi commencer pour comprendre ce dépôt, et qu'est‑ce qui | 2 / 2 |
| [armer_agent_isole](docs/armer_agent_isole.md) | L'outil doit déterminer si un agent IA lancé en isolation possède réellement toute la matière du dépôt ou… | 2 / 2 |
| [atelier_document](docs/atelier_document.md) | que dit ce document, et où exactement le dit-il ? | 2 / 2 |
| [auditeur_fichiers](docs/auditeur_fichiers.md) | Quels fichiers sont modifiés en temps réel ? | 2 / 2 |
| [auditeur_flux](docs/auditeur_flux.md) | Quels flux réseau sont actifs ? | 2 / 2 |
| [auditeur_permissions](docs/auditeur_permissions.md) | Quels fichiers ont des permissions dangereuses ? | 2 / 2 |
| [authentifier_oauth](docs/authentifier_oauth.md) | Comment obtenir un jeton d’accès pour ce service ? | 2 / 2 |
| [bac_de_travail](docs/bac_de_travail.md) | Puis‑je essayer ce code sans conséquence ? | 2 / 2 |
| [banc_agent](docs/banc_agent.md) | qu'est-ce que ce travail a réellement coûté ? | 2 / 2 |
| [banc_mesure](docs/banc_mesure.md) | cette voie est-elle vraiment plus rapide que celle-là ? | 2 / 2 |
| [cache_distribue](docs/cache_distribue.md) | Puis-je partager un cache entre processus ? | 2 / 2 |
| [calculer_numerique](docs/calculer_numerique.md) | Quel est le résultat numérique de cette expression ? | 2 / 2 |
| [carte_appels](docs/carte_appels.md) | qui appelle quoi dans ce depot, sans rien executer ? | 2 / 2 |
| [charger_modele](docs/charger_modele.md) | Quel modèle est disponible localement ? | 2 / 2 |
| [chercher_tolerant](docs/chercher_tolerant.md) | Ce fragment existe‑t‑il, même mal cité, et où exactement ? | 2 / 2 |
| [chiffrer_payload](docs/chiffrer_payload.md) | Puis-je chiffrer ce payload pour le transporter ? | 2 / 2 |
| [comparer_arbres](docs/comparer_arbres.md) | qu'est-ce qui a changé entre ces deux arbres ? | 2 / 2 |
| [compresseur_rapide](docs/compresseur_rapide.md) | Comment compresser/décompresser sans zlib ? | 2 / 2 |
| [connexion_http_bas_niveau](docs/connexion_http_bas_niveau.md) | L'outil permet-il d'effectuer des requêtes HTTP sans bloquer l'agent ? | 2 / 2 |
| [contraindre_sortie](docs/contraindre_sortie.md) | La sortie du LLM respecte‑t‑elle le format ou le schéma attendu ? | 2 / 2 |
| [contrat_mcp](docs/contrat_mcp.md) | L'outil peut-il échanger, valider, documenter et vérifier la version d'un service JSON-RPC sans bloquer… | 2 / 2 |
| [croiser_motifs](docs/croiser_motifs.md) | ces motifs se recouvrent-ils, et lequel ne servira jamais ? | 2 / 2 |
| [decompiler_compile](docs/decompiler_compile.md) | Peut-on inspecter/modifier du bytecode Python sans outils natifs ? | 2 / 2 |
| [decouvrir_api](docs/decouvrir_api.md) | comment se sert‑on de cette API que je ne connais pas ? | 2 / 2 |
| [decrire_paquet](docs/decrire_paquet.md) | Le processus d'importation utilise-t-il réellement setuptools ? | 2 / 2 |
| [detecter_os](docs/detecter_os.md) | Sur quel système cette machine tourne-t-elle ? | 2 / 2 |
| [deviner_encodage](docs/deviner_encodage.md) | Quel encodage le fichier utilise‑t‑il réellement ? | 2 / 2 |
| [dialoguer_anthropic](docs/dialoguer_anthropic.md) | Le texte généré correspond‑il à la demande du prompt ? | 2 / 2 |
| [duree_iso](docs/duree_iso.md) | La chaîne fournie est‑elle une date/heure/durée ISO 8601 valide ? | 2 / 2 |
| [ecart_declare_reel](docs/ecart_declare_reel.md) | Ce que je lis dans ce fichier est-il ce qui s'exécutera ? | 2 / 2 |
| [ecouter_evenements](docs/ecouter_evenements.md) | Quels événements ce flux émet‑il en continu ? | 2 / 2 |
| [empreinte_api](docs/empreinte_api.md) | qu'est-ce qui a cassé entre ces deux versions ? | 2 / 2 |
| [encoder_decoder](docs/encoder_decoder.md) | Comment convertir ces données entre formats binaires/textuels ? | 2 / 2 |
| [environnement_node](docs/environnement_node.md) | Peut-on exécuter un script npm dans un environnement Node isolé sans polluer le Python ? | 2 / 2 |
| [envoyer_alerte_sentry](docs/envoyer_alerte_sentry.md) | Cette erreur doit-elle être rapportée ? | 2 / 2 |
| [eprouver_env](docs/eprouver_env.md) | Ce paquet marche‑t‑il ICI, et si non, pourquoi ?  L’outil doit déterminer si les binaires natifs d’une… | 2 / 2 |
| [eprouver_mcp](docs/eprouver_mcp.md) | Le processus identifié par PID X est‑il réellement bloqué ? | 2 / 2 |
| [exposer_service](docs/exposer_service.md) | Le serveur expose-t-il correctement la spécification OpenAPI ? | 2 / 2 |
| [extracteur_entites](docs/extracteur_entites.md) | Quelles entités (noms, dates) sont présentes ? | 2 / 2 |
| [extraire_metadonnees](docs/extraire_metadonnees.md) | Quelles métadonnées ce fichier contient‑il ? | 2 / 2 |
| [extraire_pdf](docs/extraire_pdf.md) | Que contient ce PDF (texte, structure, images) ? | 2 / 2 |
| [format_harmony](docs/format_harmony.md) | Les réponses de différents modèles d'IA sont-elles cohérentes entre elles ? | 2 / 2 |
| [gabarit_sur](docs/gabarit_sur.md) | Ce texte peut‑il être construit sans risque d'injection ? | 2 / 2 |
| [garde_execution](docs/garde_execution.md) | puis-je exécuter ce code sans conséquence accidentelle ? | 2 / 2 |
| [generateur_aleatoire](docs/generateur_aleatoire.md) | Puis-je générer des nombres aléatoires sans `random` ? | 2 / 2 |
| [generer_texte](docs/generer_texte.md) | Comment produire ce texte à partir d’un modèle ? | 2 / 2 |
| [guetter_fichiers](docs/guetter_fichiers.md) | Le fichier a‑t‑il réellement changé depuis le dernier relevé ? | 2 / 2 |
| [hasher_mot_de_passe](docs/hasher_mot_de_passe.md) | Puis-je stocker ce secret de manière sécurisée ? | 2 / 2 |
| [impact_tests](docs/impact_tests.md) | quels tests dois-je relancer après ce changement ? | 2 / 2 |
| [indexer_arbre](docs/indexer_arbre.md) | où est ce symbole, et cette citation existe-t-elle vraiment ? | 2 / 2 |
| [indexeur_fichiers](docs/indexeur_fichiers.md) | Où sont stockés ces motifs dans des fichiers binaires ? | 2 / 2 |
| [inspecter_pickle](docs/inspecter_pickle.md) | que fera ce pickle si je le charge ? | 2 / 2 |
| [instrumenter_sans_casser](docs/instrumenter_sans_casser.md) | Peut-on instrumenter du code Python sans en altérer le comportement observable ? | 2 / 2 |
| [interroger_http](docs/interroger_http.md) | Interroger un service HTTP et déterminer sa réponse ainsi que les modalités de négociation. | 2 / 2 |
| [interroger_paquets](docs/interroger_paquets.md) | Quelle version, quels points d'entrée, quelle distribution ou quelles dépendances pour un paquet Python… | 2 / 2 |
| [journaliser_structure](docs/journaliser_structure.md) | Comment enregistrer les événements de manière exploitable ? | 2 / 2 |
| [json_rapide](docs/json_rapide.md) | Quelle est la performance et la compatibilité de `json_rapide` pour manipuler du JSON en production ? | 2 / 2 |
| [json_tronque](docs/json_tronque.md) | Peut‑on extraire la valeur d’une clé profonde sans charger tout le fichier ? | 2 / 2 |
| [lire_multipart](docs/lire_multipart.md) | Décoder correctement un flux multipart sans dépasser la RAM disponible. | 2 / 2 |
| [livrer_autonome](docs/livrer_autonome.md) | cet outil peut-il voyager en un seul fichier ? | 2 / 2 |
| [manipuler_archive](docs/manipuler_archive.md) | Que contient cette archive, et comment l’extraire ? | 2 / 2 |
| [manipuler_env](docs/manipuler_env.md) | Quelles variables d'environnement ce processus voit-il ? | 2 / 2 |
| [manipuler_fuseaux](docs/manipuler_fuseaux.md) | Quelle heure est‑il à cet endroit ? | 2 / 2 |
| [manipuler_tensors](docs/manipuler_tensors.md) | Comment transformer ce tenseur ? | 2 / 2 |
| [memoire_partagee](docs/memoire_partagee.md) | Peut-on partager un état ou exécuter des tâches sans bloquer le flux principal ? | 2 / 2 |
| [memoire_projet](docs/memoire_projet.md) | ce que je crois savoir de ce projet est-il encore vrai ? | 2 / 2 |
| [mesurer_dette](docs/mesurer_dette.md) | ce changement ajoute-t-il de la dette, et où ? | 2 / 2 |
| [mettre_en_cache](docs/mettre_en_cache.md) | Comment éviter de recalculer ce résultat coûteux ? | 2 / 2 |
| [montrer_borne](docs/montrer_borne.md) | comment montrer cette structure sans saturer le lecteur ? | 2 / 2 |
| [objets_amazon](docs/objets_amazon.md) | Capacité à interagir avec les services AWS sans écrire de code bas‑niveau. | 2 / 2 |
| [optimiseur_cpu](docs/optimiseur_cpu.md) | Quelles fonctions consomment le plus de CPU ? | 2 / 2 |
| [ouvrir_partout](docs/ouvrir_partout.md) | Puis‑je lire ce dépôt sans le poser sur le disque ? | 2 / 2 |
| [paralleliser](docs/paralleliser.md) | ce travail gagne-t-il à être reparti sur plusieurs interpréteurs ? | 2 / 2 |
| [parler_agents](docs/parler_agents.md) | Peut-on échanger des messages fiables entre agents sans blocage ni perte ? | 2 / 2 |
| [parser_argument_ligne](docs/parser_argument_ligne.md) | Comment interpréter ces arguments CLI ? | 2 / 2 |
| [parser_json_partiel](docs/parser_json_partiel.md) | Que contient ce flux JSON incomplet ? | 2 / 2 |
| [parser_markdown](docs/parser_markdown.md) | Quelle est la structure de ce document Markdown ? | 2 / 2 |
| [parser_yaml_toml](docs/parser_yaml_toml.md) | Que contient ce fichier de configuration ? | 2 / 2 |
| [planifier_taches](docs/planifier_taches.md) | Quand cette tâche doit‑elle s’exécuter ? | 2 / 2 |
| [poids_quantifies](docs/poids_quantifies.md) | Comment réduire la taille mémoire d'un tenseur ou modèle PyTorch sans perdre trop de précision ? | 2 / 2 |
| [pont_outils](docs/pont_outils.md) | cet outil est-il utilisable par un autre projet ? | 2 / 2 |
| [pool_connexions](docs/pool_connexions.md) | Comment optimiser les connexions HTTP pour réduire la latence et les ressources ? | 2 / 2 |
| [poser_question_interactive](docs/poser_question_interactive.md) | Comment demander une décision humaine ? | 2 / 2 |
| [prochaine_occurrence](docs/prochaine_occurrence.md) | La prochaine occurrence d'une tâche récurrente selon une expression cron. | 2 / 2 |
| [profileur_memoire](docs/profileur_memoire.md) | Quels objets consomment le plus de mémoire ? | 2 / 2 |
| [protocole_http_brut](docs/protocole_http_brut.md) | Le message HTTP est‑il strictement conforme à la RFC 7230 ? | 2 / 2 |
| [provenance](docs/provenance.md) | de quoi ce résultat dépend-il ? | 2 / 2 |
| [proxy_transparent](docs/proxy_transparent.md) | Puis-je intercepter/modifier des requêtes HTTP ? | 2 / 2 |
| [publier_mesures](docs/publier_mesures.md) | Quel(s) type(s) de métriques Prometheus peut‑on exposer, agréger ou mesurer dans un processus Python ? | 2 / 2 |
| [publier_vitrine](docs/publier_vitrine.md) | Publier une vitrine dont aucun chiffre ne peut se périmer. | 2 / 2 |
| [rassembler_verdicts](docs/rassembler_verdicts.md) | Que disent, ensemble, tous les outils passés sur ce dépôt ? | 2 / 2 |
| [reecrire_sur](docs/reecrire_sur.md) | puis-je modifier ce code sans rien perdre d'autre ? | 2 / 2 |
| [resoudre_noms](docs/resoudre_noms.md) | Quelle est l'adresse réelle de ce nom, et par quel chemin DNS ? | 2 / 2 |
| [resumer_texte](docs/resumer_texte.md) | Quel est le résumé de ce texte ? | 2 / 2 |
| [retenir_memoire](docs/retenir_memoire.md) | pourquoi cet objet est-il encore en mémoire, et qui le retient ? | 2 / 2 |
| [saisir_blocage](docs/saisir_blocage.md) | ce programme est‑il bloqué, et où exactement ? | 2 / 2 |
| [scanner_vulnerabilites](docs/scanner_vulnerabilites.md) | Ce code contient-il des patterns dangereux ? | 2 / 2 |
| [sceller](docs/sceller.md) | Ce que je scelle aujourd’hui a‑t‑il changé demain ? | 2 / 2 |
| [segmenter_sousmots](docs/segmenter_sousmots.md) | Peut-on exploiter un modèle de tokenisation pré‑entraîné sans connaître son format interne ? | 2 / 2 |
| [serialiser_vite](docs/serialiser_vite.md) | Peut‑on désérialiser et valider des données de manière sécurisée et performante ? | 2 / 2 |
| [servir_api](docs/servir_api.md) | Comment exposer ce résultat via HTTP ? | 2 / 2 |
| [servir_modele_local](docs/servir_modele_local.md) | Comment servir un modèle avec une performance optimale en local ? | 2 / 2 |
| [signer_requete_aws](docs/signer_requete_aws.md) | L'outil signe‑t‑il correctement une requête AWS SigV4 ? | 2 / 2 |
| [simulateur_erreurs](docs/simulateur_erreurs.md) | Comment un code réagit-il à des erreurs système ? | 2 / 2 |
| [simulateur_latence](docs/simulateur_latence.md) | Comment un code réagit-il à une latence réseau ? | 2 / 2 |
| [stocker_nuage](docs/stocker_nuage.md) | Comment lire/écrire/lister des objets dans un stockage cloud ? | 2 / 2 |
| [suivre_tache](docs/suivre_tache.md) | laquelle de mes N tâches parallèles a produit cette ligne ? | 2 / 2 |
| [surveiller_processus](docs/surveiller_processus.md) | Quels processus tournent, et comment les superviser ? | 2 / 2 |
| [table_en_memoire](docs/table_en_memoire.md) | Peut-on traiter des données plus grandes que la RAM disponible ? | 2 / 2 |
| [tenir_annotations](docs/tenir_annotations.md) | ce code tient‑il les promesses de ses annotations ? | 2 / 2 |
| [tenseurs_locaux](docs/tenseurs_locaux.md) | non mesuré | 2 / 2 |
| [tokeniser_texte](docs/tokeniser_texte.md) | Comment ce texte se découpe-t-il en tokens ? | 2 / 2 |
| [tracer_execution](docs/tracer_execution.md) | Quel est le chemin d’exécution de cette requête ? | 2 / 2 |
| [tracer_llm](docs/tracer_llm.md) | Quelles traces ce flux LLM a-t-il laissées ? | 2 / 2 |
| [tracer_session_llm](docs/tracer_session_llm.md) | Quels sont les prompts, réponses et métriques associés à chaque run LLM ? | 2 / 2 |
| [traducteur_automatique](docs/traducteur_automatique.md) | Comment traduire ce texte sans API externe ? | 2 / 2 |
| [transferer_modele](docs/transferer_modele.md) | Le fichier distant ou local a‑t‑il été entièrement récupéré ou vérifié ? | 2 / 2 |
| [transporter_travail](docs/transporter_travail.md) | ce travail peut-il être exécuté ailleurs, et qu'est-ce qui traverse réellement ? | 2 / 2 |
| [traquer_mort](docs/traquer_mort.md) | Quelles fonctions ce scénario n'entre‑t‑il jamais ? | 2 / 2 |
| [validateur_csv](docs/validateur_csv.md) | Ce CSV est-il valide selon les règles définies ? | 2 / 2 |
| [validateur_formule](docs/validateur_formule.md) | Ces deux formules mathématiques sont-elles équivalentes ? | 2 / 2 |
| [validateur_hash](docs/validateur_hash.md) | Ce fichier correspond-il à ce hash ? | 2 / 2 |
| [validateur_i18n](docs/validateur_i18n.md) | Ces chaînes sont-elles bien encodées ? | 2 / 2 |
| [validateur_regex](docs/validateur_regex.md) | Cette regex est-elle valide ? | 2 / 2 |
| [valider_donnees](docs/valider_donnees.md) | Cette donnée est-elle conforme au schéma attendu ? | 2 / 2 |
| [valider_email](docs/valider_email.md) | Cette adresse email est-elle valide ? | 2 / 2 |
| [valider_noyau](docs/valider_noyau.md) | Le flux ou le document respecte-t-il le schéma fourni ? | 2 / 2 |
| [valider_schema_json](docs/valider_schema_json.md) | Le JSON cible respecte-t-il le schéma donné ? | 2 / 2 |
| [verifier_code_genere](docs/verifier_code_genere.md) | ce code généré fait-il ce que l'original faisait, sans danger ? | 2 / 2 |
| [verifier_docstring](docs/verifier_docstring.md) | cette docstring décrit-elle la fonction qui existe ? | 2 / 2 |
| [verifier_exemples](docs/verifier_exemples.md) | les exemples de cette documentation sont-ils vrais ? | 2 / 2 |
| [verifier_formule](docs/verifier_formule.md) | ces deux expressions calculent-elles la meme chose ? | 2 / 2 |
| [verifier_imports](docs/verifier_imports.md) | ces imports existent-ils, et si non, que faut-il en faire ? | 2 / 2 |
| [verifier_signature_jwt](docs/verifier_signature_jwt.md) | Ce token JWT est‑il valide ? | 2 / 2 |
| [verrouiller_ressource](docs/verrouiller_ressource.md) | Vérifie si une ressource (fichier) est déjà utilisée par un autre processus. | 2 / 2 |
| [voir_image](docs/voir_image.md) | Que montre cette image, et où sont les objets/contours ? | 2 / 2 |
| [yaml_sur](docs/yaml_sur.md) | Peut-on manipuler du YAML sans perdre de métadonnées (types, commentaires, flux) ? | 2 / 2 |

</details>

## Comment c'est mesuré
- `docker build .` : rejoue la porte et le juge À L'INTÉRIEUR d'une image épinglée par empreinte. Si un seul outil échoue, l'image n'existe pas.
- `mesures/porte_qualite.py` : 15 contrôles de forme sur chaque outil.
- `mesures/test_isolation.py` : chaque outil est copié seul dans un dossier temporaire, appelé de seize façons, avec détection de fuite en lecture, écriture et réseau.
Un **VERDICT** peut être : LIVRABLE, FORWARD KO, REVERSE KO, FUITE.

*Verdicts établis sur 2 plateformes : Windows 11 et Linux (conteneur)*

## Ce que cette boîte NE fait PAS
⚠️ **« LIVRABLE » ne veut pas dire « juste ».** Le juge prouve qu'un outil répond, refuse proprement ce qu'il doit refuser, et ne sort pas de son périmètre. Il ne prouve pas que sa réponse est la bonne : c'est à vous de lire la question qu'il déclare et de juger si elle est la vôtre.

⚠️ **Mesuré sur une machine, pas sur toutes.** 34 distributions sur 188 sont bloquées par une politique système sur la machine de référence (18.1 %) ; les outils qui les emploient fonctionnent en mode dégradé et le disent.

## Comment cette page a été produite

Ce README, la bannière, le `pyproject.toml` et le `Dockerfile` sont
engendrés depuis les mesures. Aucun chiffre n'y est saisi à la main :

```
python publier_vitrine.py engendrer <cible> --racine .
```

Relancer la commande après une nouvelle mesure met la page à jour.
Un chiffre absent s'écrit « non mesuré », jamais une valeur plausible.

## Licence

[AGPL-3.0-or-later](LICENSE). En clair : vous pouvez utiliser, modifier 
et redistribuer ce code, y compris en le faisant tourner comme service 
réseau — à condition de publier vos modifications sous la même licence.

Copyright (C) 2026 SOVRALYS LLC — Enzo C. Di Bacco (KinSushi). All rights reserved.
