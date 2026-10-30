import json
import unittest
from pathlib import Path

from haunted.engine import HINT_AFTER, MAX_CHANGES, Game, same_word
from haunted.world import load, parse

SAMPLES = Path(__file__).resolve().parent.parent / "worlds"


def new_game(language: str = "en") -> Game:
    return Game(load(SAMPLES / f"sample-{language}.json"))


def game_with(**extra: list) -> Game:
    data = json.loads((SAMPLES / "sample-en.json").read_text(encoding="utf-8"))
    for key, things in extra.items():
        data[key] += things
    return Game(parse(data))


def change(kind: str, target: str = "", **extra) -> dict:
    return {"kind": kind, "target": target, **extra}


def ready_to_win(game: Game) -> Game:
    game.play([change("move", "kitchen"), change("take", "rusty_key"), change("move", "cellar")])
    game.play([change("take", "pocket_watch"), change("clue", "")])
    game.play([change("move", "library"), change("clue", ""), change("move", "attic")])
    return game


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
        game = ready_to_win(new_game())
        [result] = game.play([change("give", "silver pocket watch", to="ghost")])
        self.assertTrue(result.accepted)
        self.assertEqual(game.ending, "won")
        self.assertEqual(game.play([change("move", "hall")]), [])

    def test_the_ghost_takes_its_object_whatever_the_player_calls_it(self):
        for name in ("B", "the ghost", "him", "Barnaby the ghost", ""):
            game = ready_to_win(new_game())
            game.play([change("give", "pocket_watch", to=name)])
            self.assertEqual((name, game.ending), (name, "won"))

    def test_a_creature_in_the_room_still_gets_what_is_given_to_it(self):
        game = ready_to_win(new_game())
        game.play([change("give", "pocket_watch", to="the rat")])
        self.assertEqual(game.places["pocket_watch"], "rat")
        self.assertEqual(game.ending, "")

    def test_a_gift_to_a_creature_in_another_room_is_refused(self):
        for name in ("spider", "Penny"):
            game = ready_to_win(new_game())
            [result] = game.play([change("give", "pocket_watch", to=name)])
            self.assertEqual((name, result.reason, game.ending), (name, "nobody", ""))
            self.assertEqual(game.places["pocket_watch"], "player")

    def test_a_creature_named_like_the_ghost_does_not_take_its_gift(self):
        game = ready_to_win(game_with(creatures=[{"name": "Ghost Cat", "room": "attic"}]))
        [result] = game.play([change("give", "rusty_key", to="the ghost")])
        self.assertEqual(result.reason, "not_wanted")
        game.play([change("give", "rusty_key", to="ghost cat")])
        self.assertEqual(game.places["rusty_key"], "ghost_cat")
        game.play([change("give", "pocket_watch", to="the ghost")])
        self.assertEqual(game.ending, "won")

    def test_looking_for_an_object_where_it_lies_picks_it_up(self):
        game = new_game()
        game.play([change("move", "kitchen"), change("take", "rusty_key"), change("move", "cellar")])
        [result] = game.play([change("clue", "pocket_watch")])
        self.assertEqual((result.kind, result.accepted), ("take", True))
        self.assertIn("found the silver pocket watch", result.note)
        self.assertEqual(game.places["pocket_watch"], "player")
        self.assertEqual(game.hidden_clue().id, "inscription")

    def test_looking_for_an_object_elsewhere_finds_the_clue_of_the_room(self):
        game = new_game()
        game.play([change("move", "library")])
        [result] = game.play([change("clue", "pocket_watch")])
        self.assertEqual(result.name, "torn diary page")
        game.play([change("move", "hall")])
        [result] = game.play([change("clue", "pocket_watch")])
        self.assertEqual(result.reason, "not_here")

    def test_a_key_only_fits_its_own_door(self):
        game = new_game()
        game.play([change("move", "kitchen"), change("take", "rusty_key"), change("move", "library")])
        [result] = game.play([change("use", "rusty_key", to="library")])
        self.assertEqual(result.reason, "no_lock")
        self.assertIn("It opens the Cellar", result.note)
        [result] = game.play([change("use", "rusty_key", to="the door")])
        self.assertTrue(result.accepted)
        self.assertEqual(game.unlocked, ["cellar"])
        self.assertEqual(game.play([change("use", "rusty_key")]), [])

    def test_a_key_used_on_a_creature_opens_nothing(self):
        game = new_game()
        game.play([change("move", "kitchen"), change("take", "rusty_key")])
        [result] = game.play([change("use", "rusty_key", to="soup_monster")])
        self.assertTrue(result.accepted)
        self.assertEqual(game.unlocked, [])

    def test_help_points_to_the_next_step(self):
        game = new_game()
        self.assertIn("it opens with the rusty key, which is in the Kitchen", game.hint())
        game.play([change("move", "kitchen"), change("take", "rusty_key")])
        self.assertEqual(game.hint(), "The silver pocket watch is in the Cellar.")
        game.play([change("move", "cellar"), change("take", "pocket_watch")])
        self.assertIn("One is hidden in the Attic", game.hint())
        game.play([change("clue", ""), change("move", "library"), change("clue", "")])
        self.assertEqual(game.hint(), "Bring the silver pocket watch to Barnaby, in the Attic.")
        game.play([change("move", "attic")])
        [result] = game.play([change("help")])
        self.assertEqual((result.kind, result.reason), ("help", "asked"))
        self.assertIn("Give the silver pocket watch to Barnaby", result.note)

    def test_a_gift_to_a_creature_shows_in_the_hint(self):
        game = new_game()
        game.play([change("move", "kitchen"), change("take", "rusty_key")])
        game.play([change("give", "rusty_key", to="soup_monster")])
        self.assertIn("with the Soup Monster, in the Kitchen", game.hint())

    def test_a_stuck_player_gets_a_hint(self):
        game = new_game()
        for _ in range(HINT_AFTER - 1):
            self.assertEqual(game.play([]), [])
        [result] = game.play([])
        self.assertEqual((result.kind, result.reason), ("help", "stuck"))
        self.assertIn("rusty key", result.note)
        self.assertEqual(game.play([]), [])

    def test_progress_resets_the_wait_for_a_hint(self):
        game = new_game()
        for _ in range(HINT_AFTER - 1):
            game.play([])
        game.play([change("move", "kitchen")])
        for _ in range(HINT_AFTER - 1):
            self.assertEqual(game.play([]), [])

    def test_a_creature_keeps_a_gift_and_can_give_it_back(self):
        game = new_game()
        game.play([change("take", "candle"), change("move", "bedroom")])
        [result] = game.play([change("give", "candle", to="spider")])
        self.assertTrue(result.accepted)
        self.assertEqual(game.places["candle"], "spider")
        [result] = game.play([change("take", "candle")])
        self.assertTrue(result.accepted)
        self.assertEqual(game.places["candle"], "player")

    def test_an_object_named_like_a_clue_can_be_taken(self):
        game = game_with(items=[{"name": "silver music box", "room": "hall"}])
        game.play([change("take", "silver_music_box")])
        self.assertEqual(game.places["silver_music_box"], "player")
        game.play([change("move", "bedroom")])
        [result] = game.play([change("take", "music box")])
        self.assertEqual((result.kind, result.accepted), ("clue", True))

    def test_an_id_wins_over_another_thing_with_that_name(self):
        game = game_with(items=[{"id": "toy_box", "name": "music box", "room": "hall"}])
        game.play([change("move", "bedroom")])
        [result] = game.play([change("take", "music_box")])
        self.assertEqual((result.kind, result.accepted), ("clue", True))

    def test_a_creature_named_after_a_room_cannot_reach_into_it(self):
        game = game_with(creatures=[{"name": "Kitchen Monster", "room": "hall"}])
        [result] = game.play([change("take", "rusty key")])
        self.assertEqual(result.reason, "not_here")
        self.assertEqual(game.carried(), [])

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
        game.play([])
        copy = Game.restore(game.world, game.snapshot())
        self.assertEqual(copy.state(), game.state())
        self.assertEqual(copy.quiet_turns, 1)
        older = game.snapshot()
        del older["quiet_turns"]
        self.assertEqual(Game.restore(game.world, older).quiet_turns, 0)

    def test_word_matching(self):
        self.assertTrue(same_word("photo", "photograph"))
        self.assertTrue(same_word("key", "keys"))
        self.assertFalse(same_word("book", "boot"))
        self.assertFalse(same_word("music", "museum"))


if __name__ == "__main__":
    unittest.main()
