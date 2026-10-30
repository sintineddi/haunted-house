import unittest
from pathlib import Path

from haunted.engine import MAX_CHANGES, Game, same_word
from haunted.world import load

SAMPLES = Path(__file__).resolve().parent.parent / "worlds"


def new_game(language: str = "en") -> Game:
    return Game(load(SAMPLES / f"sample-{language}.json"))


def change(kind: str, target: str = "", **extra) -> dict:
    return {"kind": kind, "target": target, **extra}


class EngineTest(unittest.TestCase):
    def test_new_game_starts_in_the_hall_with_empty_pockets(self):
        game = new_game()
        self.assertEqual(game.room, "hall")
        self.assertEqual(game.carried(), [])
        self.assertEqual(game.courage, 3)

    def test_locked_room_needs_its_key(self):
        game = new_game()
        [result] = game.play([change("move", "cellar")])
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "locked")
        self.assertIn("rusty key", result.note)
        self.assertEqual(game.room, "hall")

    def test_the_key_opens_the_cellar_on_the_way_in(self):
        game = new_game()
        game.play([change("move", "kitchen"), change("take", "rusty_key")])
        [result] = game.play([change("move", "cellar")])
        self.assertTrue(result.accepted)
        self.assertEqual(game.room, "cellar")
        self.assertIn("cellar", game.unlocked)

    def test_an_object_must_be_in_the_room_to_be_taken(self):
        game = new_game()
        [result] = game.play([change("take", "rusty_key")])
        self.assertEqual(result.reason, "not_here")
        self.assertEqual(game.carried(), [])

    def test_an_invented_object_does_not_exist(self):
        game = new_game()
        [result] = game.play([change("use", "magic sword", to="ghost")])
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "unknown")

    def test_names_and_articles_find_the_object(self):
        game = new_game()
        game.play([change("move", "the kitchen")])
        [result] = game.play([change("take", "the key")])
        self.assertTrue(result.accepted)
        french = new_game("fr")
        french.play([change("move", "la cuisine"), change("take", "la clé")])
        self.assertEqual([item.id for item in french.carried()], ["cle_rouillee"])

    def test_a_clue_is_found_only_in_its_room(self):
        game = new_game()
        [result] = game.play([change("clue", "photograph")])
        self.assertEqual(result.reason, "no_clue")
        game.play([change("move", "attic")])
        [result] = game.play([change("clue", "")])
        self.assertTrue(result.accepted)
        self.assertEqual(game.found, ["photograph"])
        self.assertIn("Barnaby", result.detail)

    def test_the_ghost_shows_itself_after_enough_clues(self):
        game = new_game()
        results = game.play([change("move", "attic"), change("clue", "photograph")])
        self.assertFalse(game.ghost_seen)
        game.play([change("move", "library"), change("clue", "diary_page")])
        results = game.play([change("move", "attic")])
        self.assertEqual([result.kind for result in results], ["move", "ghost"])
        self.assertTrue(game.ghost_visible)

    def test_the_ghost_only_takes_what_it_lost(self):
        game = new_game()
        game.play([change("take", "candle"), change("move", "attic"), change("clue", "")])
        [result] = game.play([change("give", "candle", to="ghost")])
        self.assertEqual(result.reason, "ghost_hidden")
        game.play([change("move", "library"), change("clue", ""), change("move", "attic")])
        [result] = game.play([change("give", "candle", to="Barnaby")])
        self.assertEqual(result.reason, "not_wanted")
        self.assertIn("candle", [item.id for item in game.carried()])

    def test_giving_the_lost_object_wins(self):
        game = new_game()
        game.play([change("move", "kitchen"), change("take", "rusty_key"), change("move", "cellar")])
        game.play([change("take", "pocket_watch"), change("clue", "")])
        game.play([change("move", "library"), change("clue", ""), change("move", "attic")])
        [result] = game.play([change("give", "silver pocket watch", to="ghost")])
        self.assertTrue(result.accepted)
        self.assertEqual(game.ending, "won")
        self.assertEqual(game.play([change("move", "hall")]), [])

    def test_a_creature_keeps_a_gift_and_can_give_it_back(self):
        game = new_game()
        game.play([change("take", "candle"), change("move", "bedroom")])
        [result] = game.play([change("give", "candle", to="spider")])
        self.assertTrue(result.accepted)
        self.assertEqual(game.places["candle"], "spider")
        [result] = game.play([change("take", "candle")])
        self.assertTrue(result.accepted)
        self.assertEqual(game.places["candle"], "player")

    def test_courage_moves_one_step_per_turn(self):
        game = new_game()
        results = game.play([change("courage", amount=-3), change("courage", amount=-1)])
        self.assertEqual(len(results), 1)
        self.assertEqual(game.courage, 2)
        game.play([change("courage", amount=5)])
        game.play([change("courage", amount=5)])
        self.assertEqual(game.courage, 3)

    def test_no_courage_left_ends_the_game(self):
        game = new_game()
        for _ in range(3):
            game.play([change("courage", amount=-1)])
        self.assertEqual(game.ending, "scared")

    def test_at_most_a_few_changes_per_turn(self):
        game = new_game()
        moves = [change("move", room) for room in ("attic", "bedroom", "library", "kitchen", "hall", "attic")]
        self.assertEqual(len(game.play(moves)), MAX_CHANGES)

    def test_state_hides_where_the_ghost_is_until_it_is_seen(self):
        state = new_game().state()
        self.assertEqual(state["ghost"]["room"], "")
        self.assertEqual(state["clues"], [])
        self.assertTrue(next(room for room in state["rooms"] if room["id"] == "cellar")["locked"])

    def test_snapshot_round_trip(self):
        game = new_game()
        game.play([change("take", "candle"), change("move", "attic")])
        copy = Game.restore(game.world, game.snapshot())
        self.assertEqual(copy.state(), game.state())

    def test_word_matching(self):
        self.assertTrue(same_word("photo", "photograph"))
        self.assertTrue(same_word("key", "keys"))
        self.assertFalse(same_word("book", "boot"))
        self.assertFalse(same_word("music", "museum"))


if __name__ == "__main__":
    unittest.main()
