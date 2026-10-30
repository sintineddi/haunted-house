from __future__ import annotations

import time
from collections.abc import Iterator

from .engine import KINDS, MAX_CHANGES, MAX_COURAGE, Game, Result, overlap, words
from .ollama import Ollama, OllamaError
from .world import LANGUAGES, NAME_LIMIT, TEXT_LIMIT, emoji, text

RECENT_TURNS = 3
NO_TARGET = {"", "none", "null", "nothing", "rien", "aucun", "aucune"}
TOLD_KINDS = ("clue", "ghost")
CHANGES_SCHEMA = {
    "type": "object",
    "properties": {
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "target": {"type": "string"},
                    "to": {"type": "string"},
                    "amount": {"type": "integer"},
                },
                "required": ["kind", "target"],
            },
        }
    },
    "required": ["changes"],
}
DRAWING_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "species": {"type": "string"},
        "power": {"type": "string"},
        "weakness": {"type": "string"},
        "personality": {"type": "string"},
        "scariness": {"type": "integer"},
        "emoji": {"type": "string"},
    },
    "required": ["name", "species", "power", "weakness", "personality", "scariness", "emoji"],
}

INTERPRET_SYSTEM = """You are the rules clerk of a haunted-house adventure for children. You never tell the story.
You read what the player wants to do and list the changes to the game that the action asks for.

Kinds of change:
- move: go to another room. target = room id.
- take: pick up an object or a clue. target = its id.
- drop: put down an object the player carries. target = object id.
- give: give an object to a creature or to the ghost. target = object id, to = creature id or "ghost".
- use: use an object (light it, wave it, open something with it). target = object id, to = what it is used on (room id, creature id, "ghost") or "".
- unlock: open a locked room. target = room id.
- clue: search for something hidden (look under, open, read, dig, rummage). target = id of the clue hidden in this room, or "" if none. Eating, tasting, touching, listening or talking is not searching.
- courage: amount = -1 when the player does something creepy on purpose (puts a hand in a dark hole, reads a spell, walks into the dark), never for being kind, playing, joking or talking with a creature. +1 when they do something comforting (eat a treat, sing, make friends). Most actions have no courage change.

Rules:
- List what the player tries, even when it looks impossible. The game engine decides what happens, not you.
- If the player names a room or object that is not in the lists, put the player's word as target.
- Talking, looking around, joking or asking questions change nothing, unless the player searches something.
- Only the changes the player asks for, in their order, at most 4.
- The player may write in any language.
- Reply with JSON only."""

STORY_SYSTEM = """You are the Game Master of a Halloween adventure played by a 10-year-old child.
The story is mysterious, funny and a little spooky, never really frightening: no violence, no gore, nobody gets hurt, no creature is fought or killed. The goal is to explore the house and solve the ghost's mystery.

The game engine has already decided what happened this turn. Its facts are the truth:
- Tell every change marked DONE, as it happens.
- A change marked DID NOT HAPPEN did not happen. Tell it in a funny or mysterious way (the door won't budge, the shelf is empty) and, when the facts allow, slip in a small hint. Never mention the engine, rules or points.
- The player only carries the objects the facts list. Never give them anything, never move them to another room, never open a door, never reveal a clue the facts don't give.
- Until the facts say the ghost is visible, the ghost only shows itself as cold air, whispers or footsteps.
- Play the creatures and the ghost with the personality the facts give them.

Write in {language}, speaking to the player as "{you}".{player} 3 to 6 short sentences with simple words a 10-year-old reads easily. At most two emojis. End with a short question such as "{question}"."""

STORY_VOICE = {
    "en": {"you": "you", "question": "What do you do?", "end": "THE END"},
    "fr": {"you": "tu", "question": "Que fais-tu ?", "end": "FIN"},
}

DRAWING_SYSTEM = """A child drew a creature for a Halloween adventure game. Look at the drawing and invent its character sheet from what you see: shape, colours, eyes, teeth, wings, anything drawn.
The creature lives in a haunted house that children explore. Keep it funny and a little spooky, never gory.
- name: a fun name, 1 to 3 words.
- species: what kind of creature it is, 1 to 4 words.
- power: one short sentence.
- weakness: one short phrase, something funny.
- personality: one short sentence.
- scariness: 1 (cute) to 5 (very spooky).
- emoji: one emoji that looks like it.
Write the sheet in {language}. Reply with JSON only."""


class Master:
    def __init__(self, ollama: Ollama | None = None):
        self.ollama = ollama or Ollama()

    def interpret(self, game: Game, action: str) -> list[dict]:
        reply = self.ollama.chat_json("interpret", INTERPRET_SYSTEM, rules_view(game, action), CHANGES_SCHEMA)
        return clean_changes(reply.get("changes"))

    def tell(self, game: Game, action: str, results: list[Result]) -> Iterator[str]:
        world = game.world
        voice = STORY_VOICE[world.language]
        player = f" The player is {world.player}: match their gender in every word." if world.player else ""
        system = STORY_SYSTEM.format(
            language=LANGUAGES[world.language], you=voice["you"], question=voice["question"], player=player
        )
        return self.ollama.stream("story", system, story_view(game, action, results))

    def turn(self, game: Game, action: str) -> Iterator[dict]:
        started = time.monotonic()
        proposed = self.interpret(game, action)
        interpreted = time.monotonic()
        results = game.play(proposed)
        yield {"type": "rules", "results": [result.as_dict() for result in results]}
        story = yield from self.story_parts(game, action, results)
        untold = [result.as_dict() for result in results if not told(result, story)]
        if untold:
            yield {"type": "untold", "results": untold}
        game.turns.append(
            {
                "action": action,
                "proposed": proposed,
                "results": [result.as_dict() for result in results],
                "story": story,
                "untold": untold,
                "seconds": {
                    "interpret": round(interpreted - started, 1),
                    "story": round(time.monotonic() - interpreted, 1),
                },
            }
        )
        yield {"type": "done", "state": game.state()}

    def opening(self, game: Game) -> Iterator[dict]:
        yield {"type": "state", "state": game.state()}
        started = time.monotonic()
        story = yield from self.story_parts(game, "", [])
        game.turns.append(
            {
                "action": "",
                "proposed": [],
                "results": [],
                "story": story,
                "seconds": {"story": round(time.monotonic() - started, 1)},
            }
        )
        yield {"type": "done", "state": game.state()}

    def story_parts(self, game: Game, action: str, results: list[Result]):
        story = ""
        try:
            for part in self.tell(game, action, results):
                story += part
                yield {"type": "story", "text": part}
        except OllamaError:
            fallback = ("\n\n" if story else "") + plain_story(results, game.world.language)
            story += fallback
            yield {"type": "story", "text": fallback}
        return story

    def draw(self, image: str, language: str) -> dict:
        system = DRAWING_SYSTEM.format(language=LANGUAGES[language])
        sheet = self.ollama.chat_json("drawing", system, "Here is the drawing.", DRAWING_SCHEMA, images=[image])
        return clean_sheet(sheet)


def told(result: Result, story: str) -> bool:
    if not result.accepted or result.kind not in TOLD_KINDS:
        return True
    name = words(result.name)
    said = words(story)
    if overlap(name, said) >= min(2, len(name)):
        return True
    detail = words(result.detail)
    return bool(detail) and overlap(detail, said) * 2 >= len(detail)


def clean_changes(value: object) -> list[dict]:
    if not isinstance(value, list):
        return []
    changes = []
    for entry in value:
        if not isinstance(entry, dict) or entry.get("kind") not in KINDS:
            continue
        change = {"kind": entry["kind"], "target": str(entry.get("target") or "")[:NAME_LIMIT]}
        if change["target"].strip().lower() in NO_TARGET:
            if entry["kind"] not in ("clue", "courage"):
                continue
            change["target"] = ""
        if entry.get("to"):
            change["to"] = str(entry["to"])[:NAME_LIMIT]
        if entry["kind"] == "courage":
            try:
                change["amount"] = int(entry.get("amount") or 0)
            except (TypeError, ValueError):
                continue
        changes.append(change)
    return changes[:MAX_CHANGES]


def clean_sheet(sheet: dict) -> dict:
    try:
        scariness = int(sheet.get("scariness") or 1)
    except (TypeError, ValueError):
        scariness = 1
    return {
        "name": text(sheet.get("name"), NAME_LIMIT) or "?",
        "species": text(sheet.get("species"), NAME_LIMIT),
        "power": text(sheet.get("power"), TEXT_LIMIT),
        "weakness": text(sheet.get("weakness"), TEXT_LIMIT),
        "personality": text(sheet.get("personality"), TEXT_LIMIT),
        "scariness": min(5, max(1, scariness)),
        "emoji": emoji(sheet.get("emoji"), "👾")[:2],
    }


def rules_view(game: Game, action: str) -> str:
    world = game.world
    lines = ["Rooms:"]
    for room in world.rooms:
        marks = []
        if room.id == game.room:
            marks.append("the player is here")
        if game.is_locked(room):
            marks.append("locked")
        lines.append(f"- {room.id} = {room.name}" + (f" ({', '.join(marks)})" if marks else ""))
    lines.append("Objects:")
    for item in world.items:
        place = game.places[item.id]
        where = "carried by the player" if place == "player" else "here" if place == game.room else "elsewhere"
        lines.append(f"- {item.id} = {item.name} ({where})")
    creatures = game.creatures_here()
    lines.append("Creatures here: " + (", ".join(f"{c.id} = {c.name}" for c in creatures) if creatures else "none"))
    hidden = game.hidden_clue()
    lines.append(f"Clue hidden here: {hidden.id} = {hidden.name}" if hidden else "Clue hidden here: none")
    lines.append('The ghost (id "ghost") is ' + ("visible here." if game.ghost_visible else "not visible here."))
    lines.append("")
    lines.append(f"The player writes: {action}")
    return "\n".join(lines)


def story_view(game: Game, action: str, results: list[Result]) -> str:
    world = game.world
    room = world.room(game.room)
    ghost = world.ghost
    lines = [
        f"House: {world.title}",
        f"Rooms of the house: {', '.join(r.name + (' (locked)' if game.is_locked(r) else '') for r in world.rooms)}",
    ]
    lines.append(f"The player is in the {room.name}. {room.look}")
    here = game.items_here()
    lines.append(
        "Objects lying in this room, NOT picked up: "
        + (", ".join(f"{item.name} {item.emoji}" for item in here) if here else "none")
    )
    for creature in game.creatures_here():
        lines.append(f"Creature in this room: {creature.name} {creature.emoji}. {creature.personality}")
    hidden = game.hidden_clue()
    if hidden:
        lines.append(
            f"Hidden in this room, not found yet: the {hidden.name}. You may hint where to search; never reveal it."
        )
    carried = game.carried()
    lines.append(
        "In the player's pocket: "
        + (", ".join(f"{item.name} {item.emoji}" for item in carried) if carried else "nothing, it is empty")
    )
    for item in carried:
        if item.about:
            lines.append(f"About the {item.name}: {item.about}")
    lines.append(f"Courage: {game.courage} of {MAX_COURAGE}")
    found_now = {result.name for result in results if result.kind == "clue" and result.accepted}
    for clue_id in game.found:
        clue = next(clue for clue in world.clues if clue.id == clue_id)
        if clue.name not in found_now:
            lines.append(f"Clue found earlier, the {clue.name}: {clue.reveals}")
    if game.ghost_visible:
        lines.append(
            f"The ghost {ghost.name} is visible in this room and the player can see it clearly. Who it was: {ghost.who} "
            f"Personality: {ghost.personality} It is afraid of {ghost.fear}. "
            f"It is looking for the {world.item(ghost.wants).name}."
        )
    elif game.room == ghost.room:
        lines.append(
            "The ghost haunts this room but stays invisible: only signs of it (cold air, whispers, a moving shadow)."
        )
    else:
        lines.append("The ghost is not in this room.")
    lines.append("")
    if not action:
        lines.append(
            "This is the start of the game. Welcome the player in front of the house and describe the first room."
        )
    else:
        lines.append("What happened this turn, decided by the game engine:")
        if not results:
            lines.append(
                "- No change: the player looks, talks or imagines. Answer with atmosphere, characters and hints only."
            )
        for result in results:
            lines.append(("- DONE: " if result.accepted else "- DID NOT HAPPEN: ") + result.note)
        if game.ending == "won":
            lines.append(
                "THE GAME IS WON. Write a warm, happy ending of 5 to 7 sentences where the ghost is finally at peace, "
                f"and finish with {STORY_VOICE[world.language]['end']}."
            )
        elif game.ending == "scared":
            lines.append(
                "THE GAME IS OVER: the player ran out of the house. Write a gentle, funny ending that invites them to try again."
            )
        lines.append("")
        lines.append("Last turns:")
        for turn in game.turns[-RECENT_TURNS:]:
            if turn["action"]:
                lines.append(f"Player: {turn['action']}")
            lines.append(f"Game Master: {turn['story']}")
        lines.append("")
        lines.append(f"The player now writes: {action}")
    return "\n".join(lines)


def plain_story(results: list[Result], language: str) -> str:
    done = [result.note for result in results if result.accepted]
    failed = [result.note for result in results if not result.accepted]
    if language == "fr":
        intro = "Le maître du jeu s'est endormi une seconde. Voici ce qui s'est passé :"
        question = "Que fais-tu ?"
    else:
        intro = "The Game Master dozed off for a second. Here is what happened:"
        question = "What do you do?"
    return " ".join([intro, *done, *failed, question])
