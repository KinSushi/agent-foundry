# Relais — état au 2026-10-02 (session interrompue faute de crédit)

## Fait, vérifié, poussé
- 439a2c4 : juge (FUITE/NON EPROUVE → code non nul), porte (--json vide → 3), mesures/eprouver_instruments.py (5 étalons). CI #6 verte ; image Docker construite : 5/5 étalons, 142/142 LIVRABLE.
- 8f7535d : badges README calculés dans publier_vitrine.

## Commité ici, NON revalidé
- Dockerfile : USER agent (uid 10001) + HEALTHCHECK NONE ; verifier.yml : permissions contents: read ; générateur aligné. La reconstruction Docker qui valide useradd n'a pas fini : relancer `docker build .`.

## atelier/en_attente/ : outils NON vérifiés (ne pas mettre dans outils/ tels quels)
Écrits par des agents ; leurs auteurs annoncent porte 15/15 + juge LIVRABLE, mais la vérification adversariale indépendante n'est pas terminée.
- Lots vague 1 dont l'auteur a fini : A, C, E, H, I, J (rapports dans l'historique de session).
- Lots B, D, F, G et vague 2 (K–T) : partiels ou absents.
Pour chacun : `python mesures/porte_qualite.py atelier/en_attente/X.py`, copier dans outils/, `python mesures/test_isolation.py outils/X.py`, puis vérifier la justesse avant intégration.

## Outillage pour reprendre
- atelier/BRIEF_COMMUN.md : consigne commune des rédacteurs.
- atelier/workflow_lot_outils.js : workflow écrire→vérifier→corriger→contre-vérifier (args : lot, mode ecrire|verifier, theme, outils[{nom, consigne}]). Python de référence : CPython 3.14.7.

## Constats ouverts
- F5 ANCRAGE est INAPPLICABLE pour 142/142 outils (comparaison du nom nu « valide.py » à des chemins complets) : décision de conception à prendre.
- Actions GitHub non épinglées par SHA.
- Fiches docs/, README, pyproject : à régénérer après intégration (le générateur a besoin des artefacts de mesure Windows pour « 2 plateformes »).
