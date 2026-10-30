import io
import json
import tempfile
import unittest
from pathlib import Path

from haunted.master import Master
from server import Handler, House
from tests.test_master import FakeOllama


class Hangup(io.BytesIO):
    def __init__(self, writes: int):
        super().__init__()
        self.writes = writes

    def write(self, data: bytes) -> int:
        if not self.writes:
            raise BrokenPipeError
        self.writes -= 1
        return super().write(data)


def handler(house: House, writes: int) -> Handler:
    handler = Handler.__new__(Handler)
    handler.house = house
    handler.wfile = Hangup(writes)
    handler.request_version = "HTTP/1.1"
    handler.requestline = "POST /api/turn HTTP/1.1"
    return handler


class ServerTest(unittest.TestCase):
    def test_a_turn_is_kept_whole_when_the_page_goes_away_during_the_rules(self):
        self.check_turn_kept(writes=2)

    def test_a_turn_is_kept_whole_when_the_page_goes_away_during_the_story(self):
        self.check_turn_kept(writes=3)

    def check_turn_kept(self, writes: int):
        with tempfile.TemporaryDirectory() as folder:
            fake = FakeOllama([{"kind": "take", "target": "candle"}], ["You take", " the candle."])
            house = House(Path(folder), Master(fake))
            game = house.new_game()
            handler(house, writes).stream(game, lambda: house.master.turn(game, "I take the candle"))
            saved = json.loads((Path(folder) / "games" / f"{game.id}.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["places"]["candle"], "player")
        self.assertEqual([turn["story"] for turn in saved["turns"]], ["You take the candle."])
        self.assertFalse(house.locks[game.id].locked())


if __name__ == "__main__":
    unittest.main()
