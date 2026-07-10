"""Tests for Wikimedia Commons vessel photo helpers."""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utilities import vessel_photo  # noqa: E402


class TestVesselPhotoHelpers(unittest.TestCase):
    def test_normalize_and_key(self):
        self.assertEqual(vessel_photo._normalize_name("  queen  mary  2  "), "QUEEN MARY 2")
        k1 = vessel_photo._cache_key("Queen Mary 2", "", 123)
        k2 = vessel_photo._cache_key("QUEEN MARY 2", "", "123")
        self.assertEqual(k1, k2)

    def test_search_queries_require_maritime_qualifier(self):
        qs = vessel_photo._search_queries("Ever Given", "9811000")
        self.assertTrue(any("IMO 9811000" in q for q in qs))
        self.assertTrue(all("filetype:bitmap" in q for q in qs))
        # No bare name-only query
        self.assertFalse(any(q.endswith("Ever Given") for q in qs))
        self.assertTrue(any("ship" in q or "vessel" in q for q in qs))

    def test_rejects_short_ambiguous_names(self):
        self.assertFalse(vessel_photo._name_is_searchable("ROSE"))
        self.assertFalse(vessel_photo._name_is_searchable("STAR"))
        self.assertFalse(vessel_photo._name_is_searchable("RACOON"))
        self.assertFalse(vessel_photo._name_is_searchable("EAGLE"))
        # Single-word animal name is OK only with an IMO
        self.assertTrue(vessel_photo._name_is_searchable("RACOON", imo="9123456"))
        self.assertTrue(vessel_photo._name_is_searchable("QUEEN MARY 2"))
        self.assertEqual(vessel_photo._search_queries("ROSE"), [])
        self.assertEqual(vessel_photo._search_queries("RACOON"), [])

    def test_rejects_person_like_names_without_imo(self):
        self.assertFalse(vessel_photo._name_is_searchable("DR RAY"))
        self.assertFalse(vessel_photo._name_is_searchable("DR. RAY"))
        self.assertFalse(vessel_photo._name_is_searchable("CAPT SMITH"))
        self.assertTrue(vessel_photo._name_is_searchable("DR RAY", imo="9123456"))
        self.assertEqual(vessel_photo._search_queries("DR RAY"), [])
        # First+Last vessel names stay searchable (need title ship cue later)
        self.assertTrue(vessel_photo._name_is_searchable("MATTHEW TURNER"))
        self.assertTrue(vessel_photo._looks_like_person_name("MATTHEW TURNER"))
        self.assertTrue(vessel_photo._requires_title_maritime("MATTHEW TURNER"))

    def test_ship_token_not_in_shipbuilder(self):
        self.assertFalse(vessel_photo._title_has_maritime_cue(
            "Matthew Turner (shipbuilder).jpg"
        ))
        self.assertTrue(vessel_photo._title_has_maritime_cue(
            "Matthew Turner schooner.jpg"
        ))
        self.assertTrue(vessel_photo._title_has_maritime_cue(
            "Matthew Turner sailing ship.jpg"
        ))

    def test_pick_best_rejects_shipbuilder_portrait(self):
        pages = {
            "1": {
                "title": "File:Matthew Turner (shipbuilder).jpg",
                "imageinfo": [{
                    "mime": "image/jpeg",
                    "width": 1600,
                    "height": 900,
                    "url": "person",
                    "extmetadata": {
                        "ImageDescription": {"value": "Matthew Turner, shipbuilder"},
                    },
                }],
            },
        }
        self.assertIsNone(
            vessel_photo._pick_best_page(pages, name="MATTHEW TURNER")
        )

    def test_matthew_turner_is_pinned(self):
        pinned = vessel_photo._VESSEL_PINNED.get("MATTHEW TURNER", "")
        self.assertIn("Mamma mia!", pinned)

    def test_cape_hudson_is_pinned(self):
        pinned = vessel_photo._VESSEL_PINNED.get("CAPE HUDSON", "")
        self.assertIn("MV Cape Hudson", pinned)

    def test_cache_rejects_wrong_pin(self):
        entry = {
            "filter_version": vessel_photo.FILTER_VERSION,
            "title": "Matthew Turner (shipbuilder).jpg",
            "path": "/tmp/nope.jpg",
            "miss": False,
        }
        self.assertFalse(
            vessel_photo._cache_entry_still_valid(entry, name="MATTHEW TURNER")
        )
        self.assertFalse(
            vessel_photo._cache_entry_still_valid(
                {
                    "filter_version": vessel_photo.FILTER_VERSION,
                    "title": "Some army tank.jpg",
                    "path": "/tmp/nope.jpg",
                    "miss": False,
                },
                name="CAPE HUDSON",
            )
        )

    def test_pick_best_rejects_people_photos(self):
        pages = {
            "1": {
                "title": "File:Dr. Ray Smith portrait.jpg",
                "imageinfo": [{
                    "mime": "image/jpeg",
                    "width": 1600,
                    "height": 900,
                    "url": "a",
                    "extmetadata": {
                        "ImageDescription": {"value": "Dr. Ray at the harbor marina"},
                    },
                }],
            },
            "2": {
                "title": "File:People on boat with Dr Ray.jpg",
                "imageinfo": [{
                    "mime": "image/jpeg",
                    "width": 1800,
                    "height": 1000,
                    "url": "b",
                    "extmetadata": {},
                }],
            },
        }
        self.assertIsNone(vessel_photo._pick_best_page(pages, name="DR RAY"))

    def test_pick_best_accepts_dr_ray_ship_with_imo(self):
        pages = {
            "1": {
                "title": "File:Dr Ray ferry ship.jpg",
                "imageinfo": [{
                    "mime": "image/jpeg",
                    "width": 1600,
                    "height": 900,
                    "url": "ship",
                    "extmetadata": {},
                }],
            },
        }
        best = vessel_photo._pick_best_page(pages, name="DR RAY", imo="9123456")
        self.assertIsNotNone(best)
        self.assertIn("ferry ship", best["page"]["title"].lower())


    def test_pick_best_rejects_wildlife_for_ship_name(self):
        pages = {
            "1": {
                "title": "File:Raccoon in a tree.jpg",
                "imageinfo": [{
                    "mime": "image/jpeg",
                    "width": 1600,
                    "height": 900,
                    "url": "a",
                    "extmetadata": {
                        "ImageDescription": {"value": "A raccoon near the ship dock"},
                    },
                }],
            },
            "2": {
                "title": "File:Racoon animal portrait.jpg",
                "imageinfo": [{
                    "mime": "image/jpeg",
                    "width": 1800,
                    "height": 1000,
                    "url": "b",
                    "extmetadata": {},
                }],
            },
        }
        self.assertIsNone(vessel_photo._pick_best_page(pages, name="RACOON"))

    def test_pick_best_accepts_named_ship_with_title_cue(self):
        pages = {
            "1": {
                "title": "File:Racoon cargo ship at Rotterdam.jpg",
                "imageinfo": [{
                    "mime": "image/jpeg",
                    "width": 1600,
                    "height": 900,
                    "url": "ship",
                    "extmetadata": {},
                }],
            },
        }
        best = vessel_photo._pick_best_page(pages, name="RACOON")
        self.assertIsNotNone(best)
        self.assertIn("cargo ship", best["page"]["title"].lower())

    def test_cache_rejects_old_wildlife_hit(self):
        entry = {
            "filter_version": 1,
            "title": "Raccoon in forest.jpg",
            "path": "/tmp/nope.jpg",
            "miss": False,
        }
        self.assertFalse(
            vessel_photo._cache_entry_still_valid(entry, name="RACOON")
        )

    def test_pick_best_rejects_non_maritime(self):
        pages = {
            "1": {
                "title": "File:Rose flower garden.jpg",
                "imageinfo": [{
                    "mime": "image/jpeg",
                    "width": 1600,
                    "height": 900,
                    "url": "a",
                    "extmetadata": {},
                }],
            },
            "2": {
                "title": "File:Horse racing at Ascot.jpg",
                "imageinfo": [{
                    "mime": "image/jpeg",
                    "width": 1600,
                    "height": 900,
                    "url": "b",
                    "extmetadata": {},
                }],
            },
        }
        self.assertIsNone(vessel_photo._pick_best_page(pages, name="ROSE"))

    def test_pick_best_prefers_named_ship(self):
        pages = {
            "1": {
                "title": "File:Random logo.png",
                "imageinfo": [{"mime": "image/png", "width": 800, "height": 800, "url": "a", "extmetadata": {}}],
            },
            "2": {
                "title": "File:Ever Given container ship.jpg",
                "imageinfo": [{
                    "mime": "image/jpeg",
                    "width": 1600,
                    "height": 900,
                    "thumburl": "thumb",
                    "url": "b",
                    "extmetadata": {},
                }],
            },
        }
        best = vessel_photo._pick_best_page(pages, name="Ever Given")
        self.assertIsNotNone(best)
        self.assertIn("Ever Given", best["page"]["title"])

    def test_name_match_requires_tokens(self):
        self.assertTrue(vessel_photo._name_matches_haystack(
            "QUEEN MARY 2", "queen mary 2 cruise ship in hamburg"
        ))
        self.assertFalse(vessel_photo._name_matches_haystack(
            "QUEEN MARY 2", "queen elizabeth flower show"
        ))

    def test_lookup_uses_cache_miss(self):
        key = vessel_photo._cache_key("OBSCURE BOAT XYZ", "", 1)
        with mock.patch.object(vessel_photo, "_load_meta", return_value={
            key: {"miss": True, "ts": 9e12},
        }):
            with mock.patch.object(vessel_photo, "_search_commons") as search:
                result = vessel_photo.lookup_vessel_photo(name="OBSCURE BOAT XYZ", mmsi=1)
                self.assertIsNone(result)
                search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
