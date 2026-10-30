from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, replace

from .world import GHOST, PLAYER, Clue, Creature, Item, Room, World, plain, slug

MAX_COURAGE = 3
MAX_CHANGES = 4
HINT_AFTER = 8
KINDS = ("move", "take", "drop", "give", "use", "unlock", "clue", "courage", "help")
GHOST_WORDS = {GHOST, "fantome"}
STOP_WORDS = {
    "a",
    "an",
    "the",
    "of",
    "my",
    "your",
    "le",
    "la",
    "les",
    "l",
    "un",
    "une",
    "des",
    "du",
    "de",
    "d",
    "mon",
    "ma",
    "mes",
    "ton",
    "ta",
    "tes",
}


def words(text: str) -> set[str]:
    return {word for word in plain(text).split("_") if word and word not in STOP_WORDS}


def overlap(wanted: set[str], known: set[str]) -> int:
    return sum(1 for word in wanted if any(same_word(word, other) for other in known))


def same_word(word: str, other: str) -> bool:
    if word == other:
        return True
    shorter = min(len(word), len(other))
    common = next((index for index, (left, right) in enumerate(zip(word, other)) if left != right), shorter)
    if shorter <= 4:
        return common == shorter >= 3
    return common >= shorter - shorter // 4


@dataclass
class Result:
    kind: str
    name: str
    accepted: bool
    reason: str
    note: str
    detail: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def done(kind: str, name: str, note: str, reason: str = "", detail: str = "") -> Result:
    return Result(kind, name, True, reason, note, detail)


def refused(kind: str, name: str, reason: str, note: str) -> Result:
    return Result(kind, name, False, reason, note)


class Game:
    def __init__(self, world: World, game_id: str = ""):
        self.world = world
        self.id = game_id or uuid.uuid4().hex[:12]
        self.room = world.start
        self.courage = MAX_COURAGE
        self.places = {item.id: item.room for item in world.items}
        self.found: list[str] = []
        self.visited = [world.start]
        self.unlocked: list[str] = []
        self.ghost_seen = False
        self.ending = ""
        self.quiet_turns = 0
        self.turns: list[dict] = []

    @property
    def ghost_visible(self) -> bool:
        ghost = self.world.ghost
        return self.room == ghost.room and len(self.found) >= ghost.shows_after

    def carried(self) -> list[Item]:
        return [item for item in self.world.items if self.places[item.id] == PLAYER]

    def holder(self, item: Item) -> Creature | None:
        return next((creature for creature in self.world.creatures if creature.id == self.places[item.id]), None)

    def room_of(self, item: Item) -> Room:
        holder = self.holder(item)
        return self.world.room(holder.room if holder else self.places[item.id])

    def within_reach(self, item: Item) -> bool:
        return self.places[item.id] not in (PLAYER, GHOST) and self.room_of(item).id == self.room

    def whereabouts(self, item: Item) -> str:
        holder = self.holder(item)
        room = self.room_of(item)
        return f"with {holder.name}, in the {room.name}" if holder else f"in the {room.name}"

    def items_here(self) -> list[Item]:
        return [item for item in self.world.items if self.places[item.id] == self.room]

    def creatures_here(self) -> list[Creature]:
        return [creature for creature in self.world.creatures if creature.room == self.room]

    def hidden_clue(self) -> Clue | None:
        return next((clue for clue in self.world.clues if clue.room == self.room and clue.id not in self.found), None)

    def is_locked(self, room: Room) -> bool:
        return bool(room.locked_by) and room.id not in self.unlocked

    def play(self, changes: list[dict]) -> list[Result]:
        results: list[Result] = []
        progress = self.progress()
        courage_changed = False
        for change in changes[:MAX_CHANGES]:
            if self.ending:
                break
            kind = change.get("kind")
            if kind == "courage" and courage_changed:
                continue
            if kind == "help" and any(result.kind == "help" for result in results):
                continue
            result = self.apply(change)
            if result is None:
                continue
            courage_changed = courage_changed or kind == "courage"
            results.append(result)
            self.reveal_ghost(results)
        self.reveal_ghost(results)
        self.nudge(results, progress)
        return results

    def progress(self) -> int:
        wanted_carried = self.places[self.world.ghost.wants] == PLAYER
        return len(self.visited) + len(self.found) + len(self.unlocked) + int(self.ghost_seen) + int(wanted_carried)

    def nudge(self, results: list[Result], progress: int) -> None:
        if self.ending or self.progress() > progress or any(result.kind == "help" for result in results):
            self.quiet_turns = 0
            return
        self.quiet_turns += 1
        if self.quiet_turns >= HINT_AFTER:
            self.quiet_turns = 0
            results.append(
                done("help", "", f"The player seems stuck. Slip this hint into the story: {self.hint()}", "stuck")
            )

    def reveal_ghost(self, results: list[Result]) -> None:
        if self.ghost_visible and not self.ghost_seen:
            self.ghost_seen = True
            name = self.world.ghost.name
            results.append(
                done("ghost", name, f"The ghost {name} shows itself to the player for the first time.", "appears")
            )

    def apply(self, change: dict) -> Result | None:
        target = str(change.get("target") or "").strip()
        to = str(change.get("to") or "").strip()
        rules = {
            "move": lambda: self.move(target),
            "take": lambda: self.take(target),
            "drop": lambda: self.drop(target),
            "give": lambda: self.give(target, to),
            "use": lambda: self.use(target, to),
            "unlock": lambda: self.unlock(target),
            "clue": lambda: self.search(target),
            "courage": lambda: self.change_courage(change.get("amount")),
            "help": lambda: self.help(),
        }
        rule = rules.get(change.get("kind"))
        return rule() if rule else None

    def move(self, target: str) -> Result | None:
        room = self.find(self.world.rooms, target)
        if room is None:
            return refused(
                "move", target, "unknown", f"The player tried to go to '{target}', but this house has no such room."
            )
        if room.id == self.room:
            return None
        note = f"The player went to the {room.name}."
        if self.is_locked(room):
            key = self.world.item(room.locked_by)
            if self.places[key.id] != PLAYER:
                return refused(
                    "move",
                    room.name,
                    "locked",
                    f"The player tried to go into the {room.name}, but its door is locked. "
                    f"It opens with the {key.name}, which the player does not have.",
                )
            self.unlocked.append(room.id)
            note = f"The player unlocked the {room.name} with the {key.name} and went in."
        self.room = room.id
        if room.id not in self.visited:
            self.visited.append(room.id)
        return done("move", room.name, note)

    def take(self, target: str) -> Result | None:
        thing = self.find([*self.world.items, *self.world.clues], target)
        if isinstance(thing, Clue):
            return self.search(thing.id)
        if isinstance(thing, Item):
            return self.pick_up(thing)
        creature = self.find(self.world.creatures, target)
        if creature is not None:
            return refused(
                "take",
                creature.name,
                "creature",
                f"The player tried to pick up {creature.name}, who does not fit in a pocket and does not agree.",
            )
        return refused(
            "take",
            target,
            "unknown",
            f"The player tried to take '{target}', but there is no such object in this house. They do not get it.",
        )

    def pick_up(self, item: Item) -> Result | None:
        if self.places[item.id] == PLAYER:
            return None
        if not self.within_reach(item):
            return refused(
                "take", item.name, "not_here", f"The player looked for the {item.name}, but it is not in this room."
            )
        holder = self.holder(item)
        self.places[item.id] = PLAYER
        if holder:
            return done("take", item.name, f"{holder.name} gave the {item.name} back to the player.")
        return done("take", item.name, f"The player picked up the {item.name}.")

    def drop(self, target: str) -> Result | None:
        item = self.find(self.world.items, target)
        if item is None or self.places[item.id] != PLAYER:
            return self.not_carried("drop", item.name if item else target)
        self.places[item.id] = self.room
        return done("drop", item.name, f"The player put the {item.name} down in the {self.world.room(self.room).name}.")

    def give(self, target: str, to: str) -> Result | None:
        item = self.find(self.world.items, target)
        if item is None or self.places[item.id] != PLAYER:
            return self.not_carried("give", item.name if item else target)
        if not self.means_ghost(to):
            creature = self.find(self.creatures_here(), to) or self.find(self.world.creatures, to)
            if creature is not None and creature.room == self.room:
                self.places[item.id] = creature.id
                return done(
                    "give", item.name, f"The player gave the {item.name} to {creature.name}, who keeps it for now."
                )
            if creature is not None:
                return refused(
                    "give",
                    item.name,
                    "nobody",
                    f"The player wanted to give the {item.name} to {creature.name}, who is not in this room.",
                )
            if not self.ghost_visible:
                return refused(
                    "give",
                    item.name,
                    "nobody",
                    f"The player wanted to give the {item.name} to '{to}', but nobody by that name is here.",
                )
        return self.give_ghost(item)

    def means_ghost(self, to: str) -> bool:
        named = words(to)
        return bool(named) and named <= words(self.world.ghost.name) | GHOST_WORDS

    def give_ghost(self, item: Item) -> Result:
        ghost = self.world.ghost
        if not self.ghost_visible:
            return refused(
                "give",
                item.name,
                "ghost_hidden",
                f"The player held out the {item.name} to the ghost, but no ghost shows itself here. Nobody takes it.",
            )
        if item.id != ghost.wants:
            return refused(
                "give",
                item.name,
                "not_wanted",
                f"The player offered the {item.name} to {ghost.name}, but it is not what the ghost is looking for. "
                "The player keeps it.",
            )
        self.places[item.id] = GHOST
        self.ending = "won"
        return done(
            "give",
            item.name,
            f"The player gave the {item.name} to {ghost.name}. It is what the ghost was looking for: "
            "the mystery is solved and the game is won.",
            "won",
        )

    def use(self, target: str, to: str) -> Result | None:
        item = self.find(self.world.items, target)
        if item is None:
            return refused(
                "use",
                target,
                "unknown",
                f"The player tried to use '{target}', but they have no such thing. Nothing happens.",
            )
        if self.places[item.id] != PLAYER:
            return self.not_carried("use", item.name)
        doors = [room for room in self.world.rooms if room.locked_by == item.id]
        if doors:
            return self.try_key(item, doors, to)
        on = f" on {to}" if to else ""
        return done("use", item.name, f"The player used the {item.name}{on}.")

    def try_key(self, key: Item, doors: list[Room], to: str) -> Result | None:
        room = self.find(self.world.rooms, to)
        if room is None and self.names_something(to):
            return done("use", key.name, f"The player used the {key.name} on {to}.")
        if room is not None and room not in doors:
            return refused(
                "use",
                key.name,
                "no_lock",
                f"The player tried the {key.name} on the {room.name}, but there is no lock there for it. "
                f"It opens the {doors[0].name}.",
            )
        door = next((door for door in ([room] if room else doors) if self.is_locked(door)), None)
        if door is None:
            return None
        self.unlocked.append(door.id)
        return done("use", key.name, f"The player used the {key.name}: the door of the {door.name} is now unlocked.")

    def names_something(self, to: str) -> bool:
        things = [*self.world.creatures, *self.world.items, *self.world.clues]
        return self.means_ghost(to) or self.find(things, to) is not None

    def unlock(self, target: str) -> Result | None:
        room = self.find(self.world.rooms, target)
        if room is None or not self.is_locked(room):
            return None
        key = self.world.item(room.locked_by)
        if self.places[key.id] != PLAYER:
            return refused(
                "unlock",
                room.name,
                "locked",
                f"The player tried to open the {room.name}, but it is locked. It opens with the {key.name}, "
                "which the player does not have.",
            )
        self.unlocked.append(room.id)
        return done("unlock", room.name, f"The player unlocked the door of the {room.name} with the {key.name}.")

    def search(self, target: str) -> Result | None:
        thing = self.find([*self.world.items, *self.world.clues], target)
        if isinstance(thing, Item) and self.within_reach(thing):
            found = self.pick_up(thing)
            return replace(found, note=f"The player searched and found the {thing.name}. {found.note}")
        clue = thing if isinstance(thing, Clue) else None
        if clue is not None and clue.id in self.found:
            return None
        hidden = self.hidden_clue()
        if clue is None or clue.room != self.room:
            if hidden is None and isinstance(thing, Item) and self.places[thing.id] != PLAYER:
                return refused(
                    "clue",
                    thing.name,
                    "not_here",
                    f"The player looked for the {thing.name}, but it is not in this room.",
                )
            if hidden is None:
                return refused(
                    "clue", clue.name if clue else target, "no_clue", "The player searched, but nothing is hidden here."
                )
            clue = hidden
        self.found.append(clue.id)
        return done(
            "clue", clue.name, f"The player found the {clue.name}. It reveals: {clue.reveals}", detail=clue.reveals
        )

    def change_courage(self, amount: object) -> Result | None:
        try:
            step = int(amount or 0)
        except (TypeError, ValueError):
            return None
        step = (step > 0) - (step < 0)
        courage = min(MAX_COURAGE, max(0, self.courage + step))
        if courage == self.courage:
            return None
        self.courage = courage
        if courage == 0:
            self.ending = "scared"
            return done(
                "courage",
                "-1",
                "The player got one fright too many: no courage left, they run out of the house.",
                "scared",
            )
        if step < 0:
            return done("courage", "-1", "Something gave the player a fright: they lose one point of courage.")
        return done("courage", "+1", "The player feels braver: they win back one point of courage.")

    def help(self) -> Result:
        return done(
            "help",
            "",
            "The player asks for help. Slip this hint into the story, through a creature, a whisper or a sign: "
            + self.hint(),
            "asked",
        )

    def hint(self) -> str:
        world = self.world
        ghost = world.ghost
        wanted = world.item(ghost.wants)
        if self.places[wanted.id] != PLAYER:
            return self.way_to(wanted)
        if len(self.found) < ghost.shows_after:
            hidden = [clue for clue in world.clues if clue.id not in self.found]
            reachable = [clue for clue in hidden if not self.is_locked(world.room(clue.room))]
            clue = (reachable or hidden)[0]
            return (
                f"{ghost.name} only shows itself once more clues are found. "
                f"One is hidden in the {world.room(clue.room).name}."
            )
        if self.room != ghost.room:
            return f"Bring the {wanted.name} to {ghost.name}, in the {world.room(ghost.room).name}."
        return f"Give the {wanted.name} to {ghost.name}, who is right here: it is what the ghost lost."

    def way_to(self, item: Item) -> str:
        hint = f"The {item.name} is {self.whereabouts(item)}."
        room = self.room_of(item)
        if not self.is_locked(room) or self.places[room.locked_by] == PLAYER:
            return hint
        key = self.world.item(room.locked_by)
        return f"{hint} The {room.name} is locked: it opens with the {key.name}, which is {self.whereabouts(key)}."

    def not_carried(self, kind: str, name: str) -> Result:
        return refused(kind, name, "not_carried", f"The player wanted to {kind} the {name}, but they do not have it.")

    @staticmethod
    def find(things: list, target: str):
        wanted = slug(target)
        if not wanted:
            return None
        by_id = next((thing for thing in things if thing.id == wanted), None)
        if by_id is not None:
            return by_id
        by_name = next((thing for thing in things if slug(thing.name) == wanted), None)
        if by_name is not None:
            return by_name
        scored = [(overlap(words(target), words(thing.name) | words(thing.id)), thing) for thing in things]
        best = max((score for score, _ in scored), default=0)
        return next((thing for score, thing in scored if score == best), None) if best else None

    def state(self) -> dict:
        world = self.world
        ghost = world.ghost
        return {
            "id": self.id,
            "title": world.title,
            "language": world.language,
            "room": self.room,
            "rooms": [
                {
                    "id": room.id,
                    "name": room.name,
                    "emoji": room.emoji,
                    "start": room.id == world.start,
                    "door": bool(room.locked_by),
                    "locked": self.is_locked(room),
                    "visited": room.id in self.visited,
                    "creatures": [
                        {"name": creature.name, "emoji": creature.emoji, "drawing": creature.drawing}
                        for creature in world.creatures
                        if creature.room == room.id and room.id in self.visited
                    ],
                }
                for room in world.rooms
            ],
            "here": [{"id": item.id, "name": item.name, "emoji": item.emoji} for item in self.items_here()],
            "pocket": [{"id": item.id, "name": item.name, "emoji": item.emoji} for item in self.carried()],
            "courage": self.courage,
            "max_courage": MAX_COURAGE,
            "clues": [
                {"name": clue.name, "emoji": clue.emoji, "reveals": clue.reveals}
                for clue_id in self.found
                for clue in world.clues
                if clue.id == clue_id
            ],
            "clue_count": len(world.clues),
            "ghost": {
                "seen": self.ghost_seen,
                "visible": self.ghost_visible,
                "room": ghost.room if self.ghost_seen else "",
            },
            "ending": self.ending,
            "turn": len(self.turns),
        }

    def snapshot(self) -> dict:
        return {
            "id": self.id,
            "room": self.room,
            "courage": self.courage,
            "places": self.places,
            "found": self.found,
            "visited": self.visited,
            "unlocked": self.unlocked,
            "ghost_seen": self.ghost_seen,
            "ending": self.ending,
            "quiet_turns": self.quiet_turns,
            "world": self.world.as_dict(),
            "turns": self.turns,
        }

    @classmethod
    def restore(cls, world: World, data: dict) -> Game:
        game = cls(world, data["id"])
        game.room = data["room"]
        game.courage = data["courage"]
        game.places = data["places"]
        game.found = data["found"]
        game.visited = data["visited"]
        game.unlocked = data["unlocked"]
        game.ghost_seen = data["ghost_seen"]
        game.ending = data["ending"]
        game.quiet_turns = data.get("quiet_turns", 0)
        game.turns = data["turns"]
        return game
