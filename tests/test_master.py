import unittest
from pathlib import Path

from haunted.engine import Game, Result
from haunted.master import Master, clean_changes, clean_sheet, rules_view, story_view, told
from haunted.ollama import OllamaError, parse_json, repair_bytes
from haunted.world import load
from tests.test_engine import ready_to_win

SAMPLES = Path(__file__).resolve().parent.parent / "worlds"


class FakeOllama:
    def __init__(self, changes: list[dict], story: list[str], fail_story: bool = False):
        self.changes = changes
        self.story = story
        self.fail_story = fail_story
        self.prompts: list[str] = []

    def chat_json(self, kind, system, user, schema, images=None):
        self.prompts.append(user)
        return {"changes": self.changes}

    def stream(self, kind, system, user, temperature=0.8):
        self.prompts.append(system + "\n" + user)
        yield from self.story
        if self.fail_story:
            raise OllamaError("gone")


def new_game() -> Game:
    return Game(load(SAMPLES / "sample-en.json"))


class MasterTest(unittest.TestCase):
    def test_clean_changes_keeps_known_kinds_only(self):
        changes = clean_changes(
            [
                {"kind": "fly", "target": "roof"},
                {"kind": "take", "target": "none"},
                {"kind": "clue", "target": "none"},
                {"kind": "courage", "target": "", "amount": "-1"},
                {"kind": "move", "target": "attic"},
                "nonsense",
            ]
        )
        self.assertEqual(
            changes,
            [
                {"kind": "clue", "target": ""},
                {"kind": "courage", "target": "", "amount": -1},
                {"kind": "move", "target": "attic"},
            ],
        )
        self.assertEqual(clean_changes({"kind": "move"}), [])

    def test_clean_changes_keeps_help_without_a_target(self):
        self.assertEqual(clean_changes([{"kind": "help", "target": "none"}]), [{"kind": "help", "target": ""}])

    def test_the_ghost_asks_for_what_it_lost(self):
        game = ready_to_win(new_game())
        self.assertIn('give target=pocket_watch to="ghost"', rules_view(game, "I show him the watch"))
        self.assertIn("holds out a hand", story_view(game, "I look at him", []))
        self.assertNotIn('to="ghost"', rules_view(new_game(), "I look around"))

    def test_a_turn_applies_only_what_the_engine_accepts(self):
        game = new_game()
        fake = FakeOllama([{"kind": "move", "target": "cellar"}], ["The door won't budge.", " What do you do?"])
        events = list(Master(fake).turn(game, "I go to the cellar"))
        self.assertEqual(events[0]["results"][0]["reason"], "locked")
        self.assertEqual(game.room, "hall")
        self.assertIn("DID NOT HAPPEN", fake.prompts[1])
        self.assertEqual(game.turns[-1]["story"], "The door won't budge. What do you do?")
        self.assertEqual(events[-1]["type"], "done")

    def test_a_clue_the_story_forgets_is_reported(self):
        game = new_game()
        game.play([{"kind": "move", "target": "attic"}])
        fake = FakeOllama([{"kind": "clue", "target": "photograph"}], ["The trunks are empty. What do you do?"])
        events = list(Master(fake).turn(game, "I open the trunks"))
        untold = [event for event in events if event["type"] == "untold"]
        self.assertEqual(untold[0]["results"][0]["name"], "old photograph")
        self.assertEqual(game.turns[-1]["untold"][0]["kind"], "clue")

    def test_told_accepts_a_paraphrase_or_the_quoted_clue(self):
        clue = Result(
            "clue",
            "scratched inscription",
            True,
            "",
            "",
            'Scratched into the workbench: "Every night at midnight I look for it. B."',
        )
        self.assertTrue(told(clue, 'You see scratches. It says: "Every night at midnight I look for it. B."'))
        self.assertFalse(told(clue, "The workbench is dusty."))
        photo = Result("clue", "old photograph", True, "", "", "")
        self.assertTrue(told(photo, "Inside, an old photo of a man."))
        self.assertFalse(told(photo, "Only old dust."))

    def test_the_story_falls_back_to_the_engine_notes(self):
        game = new_game()
        fake = FakeOllama([{"kind": "take", "target": "candle"}], ["You reach"], fail_story=True)
        events = list(Master(fake).turn(game, "I take the candle"))
        story = "".join(event["text"] for event in events if event["type"] == "story")
        self.assertIn("picked up the candle", story)
        self.assertTrue(story.startswith("You reach"))

    def test_story_view_separates_new_and_old_clues(self):
        game = new_game()
        game.play([{"kind": "move", "target": "attic"}])
        results = game.play([{"kind": "clue", "target": ""}])
        view = story_view(game, "I search", results)
        self.assertIn("DONE: The player found the old photograph", view)
        self.assertNotIn("Clue found earlier", view)
        self.assertIn("In the player's pocket: nothing, it is empty", view)
        self.assertIn("stays invisible", view)

    def test_rules_view_lists_ids_and_places(self):
        game = new_game()
        view = rules_view(game, "I take the candle")
        self.assertIn("- candle = candle that never goes out (here)", view)
        self.assertIn("- cellar = Cellar (locked)", view)
        self.assertTrue(view.endswith("The player writes: I take the candle"))

    def test_the_player_line_reaches_the_story_prompt(self):
        game = new_game()
        game.world.player = "a girl"
        fake = FakeOllama([], ["Hello. What do you do?"])
        list(Master(fake).opening(game))
        self.assertIn("The player is a girl", fake.prompts[0])

    def test_clean_sheet_bounds_the_drawing(self):
        sheet = clean_sheet({"name": "Fluff", "scariness": 12, "emoji": "👻👻👻", "power": "x" * 900})
        self.assertEqual(sheet["scariness"], 5)
        self.assertEqual(sheet["emoji"], "👻👻")
        self.assertEqual(len(sheet["power"]), 400)

    def test_raw_bytes_are_repaired(self):
        self.assertEqual(repair_bytes("4<0xE2><0x80><0xAF>500"), "4 500")
        self.assertEqual(parse_json('```json\n{"changes": []}\n```'), {"changes": []})


if __name__ == "__main__":
    unittest.main()
