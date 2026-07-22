"""Tests for planespotters + Commons type-fallback aircraft photo helpers."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utilities import aircraft_photo  # noqa: E402


class TestAircraftPhoto(unittest.TestCase):
    def test_normalize_hex(self):
        self.assertEqual(aircraft_photo.normalize_icao_hex("A068D1"), "a068d1")
        self.assertEqual(aircraft_photo.normalize_icao_hex("0x3C66B3"), "3c66b3")
        self.assertEqual(aircraft_photo.normalize_icao_hex(""), "")
        self.assertEqual(aircraft_photo.normalize_icao_hex("abc"), "")

    def test_normalize_type_code(self):
        self.assertEqual(aircraft_photo.normalize_type_code("EC45"), "EC45")
        self.assertEqual(aircraft_photo.normalize_type_code(" as65 "), "AS65")
        self.assertEqual(aircraft_photo.normalize_type_code(""), "")
        self.assertEqual(aircraft_photo.normalize_type_code("TOOLONG"), "")

    def test_normalize_registration(self):
        self.assertEqual(aircraft_photo.normalize_registration("12-72233"), "12-72233")
        self.assertEqual(aircraft_photo.normalize_registration(" N12345 "), "N12345")
        self.assertEqual(aircraft_photo.normalize_registration("ab"), "")

    def test_pick_image_url(self):
        photo = {
            "thumbnail_large": {"src": "https://t.plnspttrs.net/x_280.jpg"},
            "photographer": "Test",
        }
        self.assertTrue(aircraft_photo._pick_image_url(photo).endswith("_280.jpg"))

    def test_lookup_caches_miss(self):
        with mock.patch.object(aircraft_photo, "_load_meta", return_value={
            "3c66b3": {
                "miss": True,
                "ts": 9e12,
                "logic_version": aircraft_photo.PHOTO_LOGIC_VERSION,
            },
        }):
            with mock.patch("utilities.aircraft_photo.requests.get") as get:
                result = aircraft_photo.lookup_aircraft_photo("3C66B3")
                self.assertIsNone(result)
                get.assert_not_called()

    def test_stale_miss_retries_after_logic_bump(self):
        with mock.patch.object(aircraft_photo, "_load_meta", return_value={
            "3c66b3": {"miss": True, "ts": 9e12, "logic_version": 1},
        }):
            with mock.patch.object(
                aircraft_photo, "_planespotters_lookup", return_value=None
            ) as ps:
                with mock.patch.object(
                    aircraft_photo, "_lookup_type_commons", return_value=None
                ):
                    with mock.patch.object(aircraft_photo, "_store_miss"):
                        result = aircraft_photo.lookup_aircraft_photo(
                            "3C66B3", aircraft_type="EC45"
                        )
                        self.assertIsNone(result)
                        ps.assert_called()

    def test_type_search_queries_include_aliases(self):
        queries = aircraft_photo._type_search_queries("EC45")
        blob = " ".join(queries).lower()
        self.assertTrue(queries)
        self.assertIn("ec145", blob)

    def test_fetch_passes_type_and_reg(self):
        with mock.patch.object(
            aircraft_photo, "lookup_aircraft_photo", return_value=None
        ) as lookup:
            aircraft_photo.fetch_aircraft_photo_for({
                "icao_hex": "A9ADD7",
                "plane": "EC45",
                "registration": "12-72233",
            })
            lookup.assert_called_once_with(
                "a9add7",
                aircraft_type="EC45",
                registration="12-72233",
                force=False,
            )

    def test_credit_line_planespotters(self):
        self.assertEqual(
            aircraft_photo.photo_credit_line({"photographer": "Jane Doe"}),
            "© Jane Doe",
        )

    def test_credit_line_commons_type(self):
        line = aircraft_photo.photo_credit_line({
            "source": "wikimedia_commons",
            "photographer": "Alice",
            "match": "type",
            "type_code": "EC45",
        })
        self.assertIn("Alice", line)
        self.assertIn("EC45", line)

    def test_c172_is_pinned(self):
        pinned = aircraft_photo._TYPE_PINNED.get("C172", "")
        self.assertIn("Cessna 172S Skyhawk SP", pinned)

    def test_c152_is_pinned(self):
        pinned = aircraft_photo._TYPE_PINNED.get("C152", "")
        self.assertIn("Cessna 152 Aeroandes", pinned)

    def test_s22t_is_pinned(self):
        pinned = aircraft_photo._TYPE_PINNED.get("S22T", "")
        self.assertIn("Cirrus SR22T", pinned)
        self.assertEqual(
            aircraft_photo._TYPE_PINNED.get("SR22"),
            aircraft_photo._TYPE_PINNED.get("S22T"),
        )

    def test_be33_is_pinned(self):
        pinned = aircraft_photo._TYPE_PINNED.get("BE33", "")
        self.assertIn("Debonair", pinned)

    def test_cached_miss_retries_when_type_pin_exists(self):
        with mock.patch.object(aircraft_photo, "_load_meta", return_value={
            "a680d4": {
                "miss": True,
                "ts": 9e12,
                "logic_version": aircraft_photo.PHOTO_LOGIC_VERSION,
            },
        }):
            hit = {
                "miss": False,
                "path": "/tmp/type_s22t.jpg",
                "source": "wikimedia_commons",
                "match": "type",
                "type_code": "S22T",
            }
            with mock.patch.object(
                aircraft_photo, "_planespotters_lookup", return_value=None
            ):
                with mock.patch.object(
                    aircraft_photo, "_lookup_type_commons", return_value=hit
                ) as commons:
                    result = aircraft_photo.lookup_aircraft_photo(
                        "A680D4", aircraft_type="S22T"
                    )
                    self.assertIsNotNone(result)
                    commons.assert_called_once()

    def test_as65_is_pinned(self):
        pinned = aircraft_photo._TYPE_PINNED.get("AS65", "")
        self.assertIn("MH-65D Dolphin", pinned)

    def test_h47_is_pinned(self):
        pinned = aircraft_photo._TYPE_PINNED.get("H47", "")
        self.assertIn("CH-47 Chinook", pinned)
        self.assertEqual(
            aircraft_photo._TYPE_PINNED.get("CH47"),
            aircraft_photo._TYPE_PINNED.get("H47"),
        )

    def test_xa_vsl_airframe_is_pinned(self):
        pin = aircraft_photo._resolve_airframe_pin("0D0E03", "XA-VSL")
        self.assertIsNotNone(pin)
        self.assertIn("1257912", pin["page_url"])
        self.assertIn("volaris", pin["page_url"])
        self.assertEqual(
            aircraft_photo._resolve_airframe_pin("", "XA-VSL")["page_url"],
            pin["page_url"],
        )
        # Stale Commons type fallback must not stick once the pin exists.
        self.assertFalse(
            aircraft_photo._cache_entry_usable(
                {
                    "hex": "0d0e03",
                    "match": "type",
                    "type_code": "A21N",
                    "page_url": (
                        "https://commons.wikimedia.org/wiki/File:"
                        "München,_Flughafen,_D-AINT_auf_Rollbahn,_1.jpeg"
                    ),
                    "logic_version": aircraft_photo.PHOTO_LOGIC_VERSION,
                },
                type_code="A21N",
                hex_id="0d0e03",
            )
        )

    def test_rejects_gulfstream_photo_for_chinook(self):
        photo = {
            "link": (
                "https://www.planespotters.net/photo/1358748/"
                "vp-bzf-zanoshfa-capital-gulfstream-g650-gvi"
            ),
            "registration": "VP-BZF",
        }
        self.assertFalse(
            aircraft_photo._planespotters_matches_expected_type(photo, "H47")
        )
        self.assertFalse(
            aircraft_photo._cache_entry_usable(
                {
                    "source": "planespotters",
                    "page_url": photo["link"],
                    "logic_version": aircraft_photo.PHOTO_LOGIC_VERSION,
                },
                type_code="H47",
            )
        )

    def test_accepts_chinook_planespotters_photo(self):
        photo = {
            "link": (
                "https://www.planespotters.net/photo/1/"
                "12-08148-us-army-boeing-ch-47f-chinook"
            ),
        }
        self.assertTrue(
            aircraft_photo._planespotters_matches_expected_type(photo, "H47")
        )

    def test_stale_type_pin_not_usable(self):
        entry = {
            "match": "type",
            "type_code": "C172",
            "title": "702 Helicopters Cessna 172 Skyhawk N19804.jpg",
            "logic_version": aircraft_photo.PHOTO_LOGIC_VERSION,
        }
        self.assertFalse(aircraft_photo._cache_entry_usable(entry))

    def test_hex_miss_falls_back_to_commons_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            aircraft_photo._CACHE_DIR = tmp
            aircraft_photo._META_PATH = str(Path(tmp) / "index.json")
            aircraft_photo._meta = {}

            commons_hit = {
                "miss": False,
                "path": str(Path(tmp) / "a9add7_type.jpg"),
                "source": "wikimedia_commons",
                "match": "type",
                "type_code": "EC45",
                "photographer": "Commons User",
            }
            Path(commons_hit["path"]).write_bytes(b"fake-image-bytes-here")

            with mock.patch.object(
                aircraft_photo, "_planespotters_lookup", return_value=None
            ):
                with mock.patch.object(
                    aircraft_photo, "_lookup_type_commons", return_value=commons_hit
                ) as commons:
                    result = aircraft_photo.lookup_aircraft_photo(
                        "A9ADD7", aircraft_type="EC45"
                    )
                    self.assertIsNotNone(result)
                    self.assertEqual(result["source"], "wikimedia_commons")
                    commons.assert_called_once()


if __name__ == "__main__":
    unittest.main()
