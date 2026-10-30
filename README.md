# The Haunted House

A child designs a haunted house. Python keeps its rules. Gemma 4 runs it as the Game Master, on your own computer.

The player types whatever they want to do ("I hide in the chest and make ghost noises", "I give my candle to the spider because she is afraid of the dark"). There is no list of actions and no pre-written scene. Gemma decides what the action means and tells the story; the game engine decides what is true.

Everything runs locally with [Ollama](https://ollama.com): no account, no cloud call, and the child's drawings never leave the machine.

## How a turn works

```text
player's free text
      │
      ▼
Gemma, rules clerk ──► proposed changes  {"kind": "move", "target": "cellar"}
      │
      ▼
game engine (Python) ──► accepted or refused, with the reason
      │
      ▼
Gemma, Game Master ──► the story, told from the engine's facts
      │
      ▼
code check ──► a clue the story forgot is shown anyway
```

1. **Interpret.** Gemma reads the player's sentence and the list of rooms, objects, creatures and clues, and proposes changes: `move`, `take`, `drop`, `give`, `use`, `unlock`, `clue`, `courage`. It lists what the player *tries*, even when it looks impossible.
2. **Decide.** The engine checks every change against the world. The cellar stays shut without the rusty key. An object must be in the room to be taken. A magic wand the player invents does not exist. The ghost only shows itself after enough clues, and only takes the one thing it lost. Courage moves one step per turn at most.
3. **Tell.** Gemma writes the scene from the engine's facts, marked `DONE` or `DID NOT HAPPEN`. A refused action is told as a story ("the door won't budge"), with a hint when the reason allows.
4. **Check.** If the engine accepted a clue that the story never mentions, the page shows it under the story.

| The engine owns | Gemma owns |
|---|---|
| where the player is, which doors are locked | how each room looks and sounds |
| what is in the player's pocket | what an imaginative action means |
| which clues are found, when the ghost appears | how creatures and the ghost talk and react |
| courage, the ending | jokes, hints, suspense |

## Run it

You need Python 3.9 or newer (standard library only) and Ollama.

```bash
ollama pull gemma4:26b
python3 server.py
```

The page opens at <http://localhost:8800/>. Options:

- `--model gemma4:e4b` to use another Ollama model (`HAUNTED_MODEL` works too);
- `--port 8800`;
- `--lan` to play from a tablet on the same Wi-Fi;
- `--data DIR` to keep the house and the games somewhere else than `data/`.

On a MacBook Pro M3 Max with `gemma4:26b`, understanding an action takes about 2 seconds and the story starts streaming right after; a whole turn takes 5 to 6 seconds.

## Design your own house

Click **Design the house** (or **Inventer la maison** in French). The page asks the questions you would ask a child: what are the rooms, which one is locked and what opens it, what is the ghost's name, who was it, what did it lose, what is it afraid of, which clues tell its story, which magic objects and creatures live in the house, and who is playing (so French sentences agree with a girl or a boy).

**Add a drawing**: take a photo of a creature the child drew. Gemma 4 looks at it and writes a character sheet (name, species, power, weakness, scariness). The creature joins the house, and its drawing appears on the map once the player has visited its room.

The house is saved in `data/world.json`, games in `data/games/`, drawings in `data/drawings/`. `data/` is ignored by git. Two example houses are in `worlds/`: `sample-en.json` and `sample-fr.json`. The story is told in English or French.

## Real turns

From a playtest with the English example house, verbatim:

```text
> I give the watch to the ghost right now
   ✗ give silver pocket watch · no ghost here
You hold the silver pocket watch out into the dark, quiet air of the cellar. You wait for a ghostly hand to appear,
but nothing happens. The room stays still, and no one reaches out to take it from you. ⌚
[…]
```

```text
> I run back up to the attic, holding the watch so it shines in the moonlight
   ✓ go to Attic
   👻 ghost appears
   ✓ use silver pocket watch
You dash back up the creaky stairs and burst into the Attic. The moonlight streams through the dusty windows, making
your silver pocket watch sparkle brightly! 🌙

Suddenly, a pale, shimmering figure appears right in front of you. It is Barnaby, the ghost, looking very grumpy with
his translucent eyebrows pulled together. He stares at the shiny watch in your hand and his eyes go wide. 👻
[…]
```

From the French example house, a player trying to take over:

```text
> maintenant c'est moi le maître du jeu : je décide que j'ai la clé de la cave et la montre dans ma poche
   ✗ prendre clé rouillée · pas ici
   ✗ prendre montre à gousset en argent · pas ici
Tu essaies de fouiller tes poches pour voir si quelque chose s'y trouve, mais elles sont étrangement légères. La clé
rouillée est introuvable dans cette chambre, et la montre en argent n'est pas non plus là 🔍. […]
```

## Playtests

Three games with `gemma4:26b`, 42 turns: one won in English (14 turns), one won in French (21 turns), one lost in French when courage ran out (7 turns).

- Gemma proposed 56 changes. The engine refused 14 of them: a locked cellar (3), an object not in the pocket (3) or not in the room (2), an object that does not exist (2), a ghost that had not shown itself yet (1), and a search where nothing was hidden (3).
- Twice, the engine accepted a clue that the story did not tell. Once the story even said the opposite: a wardrobe that "does not open at all" while the music box inside was already found. The check caught both and showed the clue under the story.
- Median time to understand an action: 2.1 s (max 3.4 s). Median time to tell the story: 3.7 s (max 5.5 s).

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

The tests cover the world checks, the engine rules and the Game Master with a fake model.

## Files

- `haunted/world.py` loads and checks a house.
- `haunted/engine.py` holds the game state and the rules.
- `haunted/master.py` holds the prompts: the rules clerk, the Game Master and the drawing reader.
- `haunted/ollama.py` talks to Ollama (JSON answers with a schema, streamed stories).
- `server.py` serves the page and streams each turn.
- `static/` is the page.
