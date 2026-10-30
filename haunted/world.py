from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

LANGUAGES = {"en": "English", "fr": "French"}
GHOST = "ghost"
PLAYER = "player"
RESERVED = {GHOST, PLAYER}
NAME_LIMIT = 60
TEXT_LIMIT = 400
EMOJI_LIMIT = 8
MAX_ROOMS = 8
MAX_THINGS = 12


class WorldError(ValueError):
    pass


def plain(text: object) -> str:
    decomposed = unicodedata.normalize("NFD", str(text).lower())
    bare = "".join(char for char in decomposed if unicodedata.category(char) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", bare).strip("_")


def slug(text: object) -> str:
    return plain(text)[:30]


@dataclass
class Room:
    id: str
    name: str
    emoji: str = "🚪"
    look: str = ""
    locked_by: str = ""


@dataclass
class Item:
    id: str
    name: str
    room: str
    emoji: str = "✨"
    about: str = ""


@dataclass
class Clue:
    id: str
    name: str
    room: str
    reveals: str
    emoji: str = "🔎"


@dataclass
class Creature:
    id: str
    name: str
    room: str
    emoji: str = "👾"
    personality: str = ""
    drawing: str = ""


@dataclass
class Ghost:
    name: str
    room: str
    wants: str
    who: str = ""
    personality: str = ""
    fear: str = ""
    shows_after: int = 2


@dataclass
class World:
    title: str
    language: str
    start: str
    ghost: Ghost
    player: str = ""
    rooms: list[Room] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    clues: list[Clue] = field(default_factory=list)
    creatures: list[Creature] = field(default_factory=list)

    def room(self, room_id: str) -> Room:
        return next(room for room in self.rooms if room.id == room_id)

    def item(self, item_id: str) -> Item:
        return next(item for item in self.items if item.id == item_id)

    def as_dict(self) -> dict:
        return asdict(self)


def load(path: Path) -> World:
    try:
        return parse(json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as error:
        raise WorldError(f"{path.name} is not valid JSON: {error}") from None


def save(world: World, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(world.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def parse(data: object) -> World:
    if not isinstance(data, dict):
        raise WorldError("a world is a JSON object")
    language = data.get("language", "en")
    if language not in LANGUAGES:
        raise WorldError(f"language must be one of {', '.join(LANGUAGES)}")
    rooms = [
        Room(
            id=identifier(entry, "room"),
            name=text(entry.get("name"), NAME_LIMIT, "room name"),
            emoji=emoji(entry.get("emoji"), "🚪"),
            look=text(entry.get("look"), TEXT_LIMIT),
            locked_by=slug(entry.get("locked_by") or ""),
        )
        for entry in entries(data, "rooms", MAX_ROOMS)
    ]
    items = [
        Item(
            id=identifier(entry, "object"),
            name=text(entry.get("name"), NAME_LIMIT, "object name"),
            room=slug(entry.get("room") or ""),
            emoji=emoji(entry.get("emoji"), "✨"),
            about=text(entry.get("about"), TEXT_LIMIT),
        )
        for entry in entries(data, "items", MAX_THINGS)
    ]
    clues = [
        Clue(
            id=identifier(entry, "clue"),
            name=text(entry.get("name"), NAME_LIMIT, "clue name"),
            room=slug(entry.get("room") or ""),
            reveals=text(entry.get("reveals"), TEXT_LIMIT, "what the clue reveals"),
            emoji=emoji(entry.get("emoji"), "🔎"),
        )
        for entry in entries(data, "clues", MAX_THINGS)
    ]
    creatures = [
        Creature(
            id=identifier(entry, "creature"),
            name=text(entry.get("name"), NAME_LIMIT, "creature name"),
            room=slug(entry.get("room") or ""),
            emoji=emoji(entry.get("emoji"), "👾"),
            personality=text(entry.get("personality"), TEXT_LIMIT),
            drawing=drawing_name(entry.get("drawing")),
        )
        for entry in entries(data, "creatures", MAX_THINGS)
    ]
    ghost_data = data.get("ghost")
    if not isinstance(ghost_data, dict):
        raise WorldError("the world needs a ghost")
    ghost = Ghost(
        name=text(ghost_data.get("name"), NAME_LIMIT, "ghost name"),
        room=slug(ghost_data.get("room") or ""),
        wants=slug(ghost_data.get("wants") or ""),
        who=text(ghost_data.get("who"), TEXT_LIMIT),
        personality=text(ghost_data.get("personality"), TEXT_LIMIT),
        fear=text(ghost_data.get("fear"), TEXT_LIMIT),
        shows_after=whole_number(ghost_data.get("shows_after", 2)),
    )
    world = World(
        title=text(data.get("title"), NAME_LIMIT, "title"),
        language=language,
        player=text(data.get("player"), NAME_LIMIT),
        start=slug(data.get("start") or (rooms[0].id if rooms else "")),
        ghost=ghost,
        rooms=rooms,
        items=items,
        clues=clues,
        creatures=creatures,
    )
    check(world)
    return world


def check(world: World) -> None:
    if len(world.rooms) < 2:
        raise WorldError("the house needs at least two rooms")
    ids = [thing.id for group in (world.rooms, world.items, world.clues, world.creatures) for thing in group]
    repeated = sorted({identifier for identifier in ids if ids.count(identifier) > 1})
    if repeated:
        raise WorldError(f"two things share the same name: {', '.join(repeated)}")
    room_ids = {room.id for room in world.rooms}
    item_ids = {item.id for item in world.items}
    if world.start not in room_ids:
        raise WorldError(f"the starting room {world.start!r} is not in the house")
    for thing in (*world.items, *world.clues, *world.creatures, world.ghost):
        if thing.room not in room_ids:
            raise WorldError(f"{thing.name} is in a room that does not exist: {thing.room!r}")
    for room in world.rooms:
        if not room.locked_by:
            continue
        if room.id == world.start:
            raise WorldError("the starting room cannot be locked")
        if room.locked_by not in item_ids:
            raise WorldError(f"the {room.name} opens with an object that does not exist: {room.locked_by!r}")
        key_room = world.room(world.item(room.locked_by).room)
        if key_room.locked_by:
            raise WorldError(f"the key of the {room.name} must be in a room that is not locked")
    if world.ghost.wants not in item_ids:
        raise WorldError(f"the ghost is looking for an object that does not exist: {world.ghost.wants!r}")
    if not 0 <= world.ghost.shows_after <= len(world.clues):
        raise WorldError(f"the ghost can wait for 0 to {len(world.clues)} clues")


def entries(data: dict, key: str, limit: int) -> list[dict]:
    value = data.get(key, [])
    if not isinstance(value, list) or not all(isinstance(entry, dict) for entry in value):
        raise WorldError(f"{key} must be a list of objects")
    if len(value) > limit:
        raise WorldError(f"at most {limit} {key}")
    return value


def identifier(entry: dict, kind: str) -> str:
    value = slug(entry.get("id") or entry.get("name") or "")
    if not value:
        raise WorldError(f"every {kind} needs a name")
    if value in RESERVED:
        raise WorldError(f"{value!r} is a reserved name")
    return value


def text(value: object, limit: int, required: str = "") -> str:
    cleaned = " ".join(str(value or "").split())[:limit]
    if required and not cleaned:
        raise WorldError(f"the {required} is missing")
    return cleaned


def emoji(value: object, default: str) -> str:
    cleaned = str(value or "").strip()
    return cleaned[:EMOJI_LIMIT] if cleaned else default


def drawing_name(value: object) -> str:
    cleaned = str(value or "")
    return cleaned if re.fullmatch(r"[0-9a-f]{16}\.(?:jpg|png)", cleaned) else ""


def whole_number(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise WorldError("the number of clues before the ghost shows itself must be a whole number") from None
