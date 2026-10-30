import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from haunted.world import WorldError, load, parse, save, slug

SAMPLES = Path(__file__).resolve().parent.parent / "worlds"


def sample(language: str = "en") -> dict:
    return json.loads((SAMPLES / f"sample-{language}.json").read_text(encoding="utf-8"))


class WorldTest(unittest.TestCase):
    def test_samples_load(self):
        for language in ("en", "fr"):
            world = load(SAMPLES / f"sample-{language}.json")
            self.assertEqual(world.language, language)
            self.assertEqual(len(world.rooms), 6)
            self.assertEqual(len(world.clues), 4)

    def test_both_samples_share_one_shape(self):
        english, french = sample("en"), sample("fr")
        for key in ("rooms", "items", "clues", "creatures"):
            self.assertEqual(len(english[key]), len(french[key]))

    def test_slug_drops_accents(self):
        self.assertEqual(slug("Clé rouillée"), "cle_rouillee")
        self.assertEqual(slug("  Bibliothèque!  "), "bibliotheque")

    def test_references_are_normalised(self):
        data = sample()
        data["ghost"]["room"] = "Attic"
        self.assertEqual(parse(data).ghost.room, "attic")

    def test_key_inside_its_own_room_is_rejected(self):
        data = sample()
        next(item for item in data["items"] if item["id"] == "rusty_key")["room"] = "cellar"
        with self.assertRaisesRegex(WorldError, "key"):
            parse(data)

    def test_unknown_room_is_rejected(self):
        data = sample()
        data["clues"][0]["room"] = "ballroom"
        with self.assertRaisesRegex(WorldError, "does not exist"):
            parse(data)

    def test_ghost_must_want_a_real_object(self):
        data = sample()
        data["ghost"]["wants"] = "golden crown"
        with self.assertRaisesRegex(WorldError, "looking for"):
            parse(data)

    def test_two_things_cannot_share_a_name(self):
        data = sample()
        data["creatures"][0]["name"] = "Kitchen"
        data["creatures"][0].pop("id")
        with self.assertRaisesRegex(WorldError, "same name"):
            parse(data)

    def test_ghost_cannot_wait_for_more_clues_than_exist(self):
        data = sample()
        data["ghost"]["shows_after"] = 9
        with self.assertRaisesRegex(WorldError, "clues"):
            parse(data)

    def test_start_room_cannot_be_locked(self):
        data = sample()
        data["rooms"][0]["locked_by"] = "candle"
        with self.assertRaisesRegex(WorldError, "starting room"):
            parse(data)

    def test_long_text_is_cut(self):
        data = copy.deepcopy(sample())
        data["rooms"][1]["look"] = "spooky " * 200
        self.assertLessEqual(len(parse(data).rooms[1].look), 400)

    def test_reserved_names_are_refused(self):
        data = sample()
        data["creatures"][0]["id"] = "ghost"
        with self.assertRaisesRegex(WorldError, "reserved"):
            parse(data)

    def test_saves_at_the_same_time_do_not_clash(self):
        world = load(SAMPLES / "sample-en.json")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "world.json"
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _: save(world, path), range(200)))
            self.assertEqual(load(path).title, world.title)
            self.assertEqual([file.name for file in Path(folder).iterdir()], ["world.json"])


if __name__ == "__main__":
    unittest.main()
