# MultiPCB Fab — prototype KiCad 9/10

Plugin pour un projet comportant plusieurs cartes indépendantes dans **un seul fichier `.kicad_pcb`**. Il détecte les contours fermés `Edge.Cuts`, demande un nom, un nombre de couches et la qualification « front panel » pour chacun, puis propose JLCPCB, PCBWay et Generic Gerber. Chaque carte reçoit son propre `gerbers.zip` avec fichiers Gerber et perçages ; les cartes non marquées « front panel » reçoivent aussi `bom.csv` et `positions.csv` si le preset les demande. Il ne modifie jamais le PCB d'origine.

## Installation

1. Décompresser l'archive. Copier le dossier `multipcb_fab` (qui contient `__init__.py`) dans le dossier des plugins de KiCad. Sur macOS, ouvrir **Éditeur de PCB → Outils → Plugins externes → Révéler le dossier des plugins dans le Finder**. Sinon, utiliser `~/Documents/KiCad/<version>/scripting/plugins/` et créer `plugins` si nécessaire.
2. Redémarrer l'Éditeur de PCB ou actualiser les plugins externes. Lancer **Outils → Plugins externes → MultiPCB Fab — exports par carte**.
3. Enregistrer le PCB avant l'export. Vérifier dans le dialogue quelle carte correspond à chaque contour (ordre de gauche à droite), saisir les noms, confirmer les couches et cocher « Front panel » le cas échéant.
4. Après avoir choisi les cartes et le preset, le dialogue **Choisir les composants avant la BOM** affiche les empreintes des cartes à assembler. Sélectionner une ligne, utiliser **Recherche LCSC API** (si autorisée), **Chercher sur JLCPCB** dans le navigateur, ou **Saisir C…**. Le choix « Ne pas assembler » retire la pièce de la BOM et du placement. Les affectations sont enregistrées dans `<projet>.kicad_pcb.parts.json` près du PCB et rechargées au prochain export. Les champs `LCSC`, `LCSC Part #`, `JLCPCB` et `JLCPCB Part #` déjà présents sur le PCB servent de valeurs initiales. Ne pas mettre de clé API dans le projet.
5. Vérifier dans le visualiseur de JLCPCB les références, rotations et polarités avant commande.

## Limites et contrôles

- Les contours pris en charge sont des suites fermées de segments droits ou des rectangles `Edge.Cuts`. Les arcs, les découpes internes, les contours imbriqués et les pistes ou zones traversant un contour sont refusés ; rien n'est exporté dans ce cas.
- La détection ne sait pas déduire les noms « UI », « Main » et « front panel » : l'utilisateur les assigne en regardant les dimensions et l'ordre spatial.
- Toutes les cartes du même `.kicad_pcb` partagent la même pile de couches KiCad. Le choix de couches sert à **vérifier** le nombre déjà configuré, pas à créer des piles indépendantes ; pour une face avant 2 couches et un circuit principal 4 couches, utiliser deux fichiers `.kicad_pcb` séparés. Le plugin refusera une valeur différente de la pile globale.
- Le preset PCBWay produit des colonnes BOM générales : contrôler leur correspondance dans l'interface de commande PCBWay. Les corrections de rotation propres aux machines ne sont pas automatisées.
- Si le dossier `<projet>-fabrication` existe, l'export s'arrête pour ne pas écraser une commande précédente. Déplacer ou renommer ce dossier pour relancer.
- Exige l'installation `pcbnew` et `wxPython` livrée avec KiCad et un `kicad-cli` accessible dans le PATH. Prototype non exécuté dans KiCad dans cet environnement : faire un premier essai sur une copie du projet.

## Accès aux catalogues

- JLCPCB fournit une API officielle Components, accessible après inscription et approbation sur https://api.jlcpcb.com/ . Le plugin propose pour l’instant la recherche dans le navigateur et la confirmation manuelle du numéro de pièce ; l’API JLC ne peut pas être intégrée de façon fiable sans paramètres de compte et documentation d’accès.
- LCSC publie une API de recherche partenaire : https://www.lcsc.com/docs/openapi/index.html . Après obtention des accès auprès de LCSC, lancer KiCad depuis un environnement définissant `LCSC_API_KEY` et `LCSC_API_SECRET`. Le plugin signe les requêtes de recherche selon la documentation officielle et affiche les résultats reconnus. Ces secrets ne sont pas enregistrés. Les réponses exactes de l’API ne sont pas vérifiées sans compte partenaire : la saisie manuelle reste disponible.
- Vérifier chaque correspondance avec la fiche technique et le modèle d’assemblage JLCPCB. Une disponibilité chez LCSC n’établit pas à elle seule la disponibilité de la pièce dans le service PCBA de JLCPCB.
